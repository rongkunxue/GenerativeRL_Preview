#############################################################
# This QGPO model is a modification implementation from https://github.com/thu-ml/SRPO
#############################################################
import copy
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import os
from datetime import datetime

import torch.nn.functional as F
import d4rl
import gym
import numpy as np
import torch
import torch.nn as nn
from easydict import EasyDict
from rich.progress import Progress, track
from tensordict import TensorDict
from torchrl.data import TensorDictReplayBuffer
from torchrl.data.replay_buffers.samplers import SamplerWithoutReplacement
from grl.rl_modules.value_network.value_network import VNetwork, DoubleVNetwork
import wandb
from grl.agents.srpo import SRPOAgent

from grl.datasets import create_dataset

from grl.datasets.d4rl import D4RLDataset
from grl.generative_models.diffusion_model import DiffusionModel
from grl.generative_models.conditional_flow_model.optimal_transport_conditional_flow_model import (
    OptimalTransportConditionalFlowModel,
)
from grl.generative_models.conditional_flow_model.independent_conditional_flow_model import (
    IndependentConditionalFlowModel,
)
from grl.generative_models.bridge_flow_model.schrodinger_bridge_conditional_flow_model import (
    SchrodingerBridgeConditionalFlowModel,
)
from grl.rl_modules.simulators import create_simulator

from grl.rl_modules.value_network.q_network import DoubleQNetwork

from grl.utils import set_seed

from grl.utils.config import merge_two_dicts_into_newone

from grl.utils.log import log
from grl.utils.model_utils import save_model, load_model


def asymmetric_l2_loss(u, tau):
    return torch.mean(torch.abs(tau - (u < 0).float()) * u**2)

class IDQLCritic(nn.Module):
    """
    Overview:
        The critic network used in SRPO algorithm.
    Interfaces:
        ``__init__``, ``v_loss``, ``q_loss
    """

    def __init__(self, config) -> None:
        """
        Overview:
            Initialize the critic network.
        Arguments:
            config (:obj:`EasyDict`): The configuration.
        """
        super().__init__()
        self.q0 = DoubleQNetwork(config.DoubleQNetwork)
        self.q0_target = copy.deepcopy(self.q0).requires_grad_(False)
        self.vf = VNetwork(config.VNetwork)

    def v_loss(self, state, action, next_state, tau):
        """
        Overview:
            Calculate the value loss.
        Arguments:
            data (:obj:`Dict`): The input data.
            tau (:obj:`float`): The threshold.
        """

        with torch.no_grad():
            target_q = self.q0_target(action, state).detach()
            next_v = self.vf(next_state).detach()
        # Update value function
        v = self.vf(state)
        adv = target_q - v
        v_loss = asymmetric_l2_loss(adv, tau)
        return v_loss, next_v

    def q_loss(self, state, action, reward, done, next_v, discount):
        """
        Overview:
            Calculate the Q loss.
        Arguments:
            data (:obj:`Dict`): The input data.
            next_v (:obj:`torch.Tensor`): The input next state value.
            discount (:obj:`float`): The discount factor.
        """
        # Update Q function
        targets = reward + (1.0 - done.float()) * discount * next_v.detach()
        q0,q1 = self.q0.compute_double_q(action, state)
        q_loss = (
                torch.nn.functional.mse_loss(q0, targets)
                + torch.nn.functional.mse_loss(q1, targets)
            ) / 2
        return q_loss,torch.mean(q0), torch.mean(targets)
    
    def q_target_min(self,state,action):
        return  self.q0_target.compute_mininum_q(state, action)


class IDQLPolicy(nn.Module):
    """
    Overview:
        The SRPO policy network.
    Interfaces:
        ``__init__``, ``forward``, ``behaviour_policy_loss``, ``v_loss``, ``q_loss``, ``srpo_actor_loss``
    """

    def __init__(self, config: EasyDict):
        """
        Overview:
            Initialize the SRPO policy network.
        Arguments:
            config (:obj:`EasyDict`): The configuration.
        """
        super().__init__()
        self.config = config
        self.device = config.device

        self.critic = IDQLCritic(config.critic)
        self.diffusion_model = DiffusionModel(config.diffusion_model)
    
    def behaviour_policy_sample(
        self,
        state: Union[torch.Tensor, TensorDict],
        batch_size: Union[torch.Size, int, Tuple[int], List[int]] = None,
        solver_config: EasyDict = None,
        t_span: torch.Tensor = None,
        with_grad: bool = False,
    ) -> Union[torch.Tensor, TensorDict]:
        """
        Overview:
            Return the output of behaviour policy, which is the action conditioned on the state.
        Arguments:
            state (:obj:`Union[torch.Tensor, TensorDict]`): The input state.
            batch_size (:obj:`Union[torch.Size, int, Tuple[int], List[int]]`): The batch size.
            solver_config (:obj:`EasyDict`): The configuration for the ODE solver.
            t_span (:obj:`torch.Tensor`): The time span for the ODE solver or SDE solver.
            with_grad (:obj:`bool`): Whether to calculate the gradient.
        Returns:
            action (:obj:`Union[torch.Tensor, TensorDict]`): The output action.
        """
        return self.diffusion_model.sample(
            t_span=t_span,
            condition=state,
            batch_size=batch_size,
            with_grad=with_grad,
            solver_config=solver_config,
        )

    def get_action(
        self, state: Union[torch.Tensor, TensorDict]
    ) -> Union[torch.Tensor, TensorDict]:
        """
        Overview:
            Return the output of QGPO policy, which is the action conditioned on the state.
        Arguments:
            state (:obj:`Union[torch.Tensor, TensorDict]`): The input state.
        Returns:
            action (:obj:`Union[torch.Tensor, TensorDict]`): The output action.
        """
        state_rpt = torch.repeat_interleave(state, repeats=200, dim=0)
        with torch.no_grad():
            action = self.behaviour_policy_sample(state=state_rpt)
            q_value = self.critic.q_target_min(state_rpt, action).flatten()
            idx = torch.multinomial(F.softmax(q_value), 1)
        return action[idx]

    def behaviour_policy_loss(
        self,
        action: Union[torch.Tensor, TensorDict],
        state: Union[torch.Tensor, TensorDict],
        maximum_likelihood: bool = False,
    ):
        """
        Overview:
            Calculate the behaviour policy loss.
        Arguments:
            action (:obj:`torch.Tensor`): The input action.
            state (:obj:`torch.Tensor`): The input state.
        """

        return self.diffusion_model.score_matching_loss(action, state)
        
    def v_loss(
        self,
        state, action, next_state,
        tau: int = 0.9,
    ) -> torch.Tensor:
        """
        Overview:
            Calculate the Q loss.
        Arguments:
            action (:obj:`torch.Tensor`): The input action.
            state (:obj:`torch.Tensor`): The input state.
            reward (:obj:`torch.Tensor`): The input reward.
            next_state (:obj:`torch.Tensor`): The input next state.
            done (:obj:`torch.Tensor`): The input done.
            fake_next_action (:obj:`torch.Tensor`): The input fake next action.
        """
        v_loss, next_v = self.critic.v_loss(state, action, next_state, tau)
        return v_loss, next_v

    def q_loss(
        self,
        state,
        action,
        reward,
        done,
        next_v: torch.Tensor,
        discount: float = 1.0,
    ) -> torch.Tensor:
        """
        Overview:
            Calculate the Q loss.
        Arguments:
            action (:obj:`torch.Tensor`): The input action.
            state (:obj:`torch.Tensor`): The input state.
            reward (:obj:`torch.Tensor`): The input reward.
            next_state (:obj:`torch.Tensor`): The input next state.
            done (:obj:`torch.Tensor`): The input done.
            fake_next_action (:obj:`torch.Tensor`): The input fake next action.
            discount_factor (:obj:`float`): The discount factor.
        """

        loss = self.critic.q_loss(state, action, reward, done, next_v, discount)
        return loss


class IDQLAlgorithm:

    def __init__(
        self,
        config: EasyDict = None,
        simulator=None,
        dataset: D4RLDataset = None,
        model: Union[torch.nn.Module, torch.nn.ModuleDict] = None,
    ):
        """
        Overview:
            Initialize the QGPO algorithm.
        Arguments:
            config (:obj:`EasyDict`): The configuration , which must contain the following keys:
                train (:obj:`EasyDict`): The training configuration.
                deploy (:obj:`EasyDict`): The deployment configuration.
            simulator (:obj:`object`): The environment simulator.
            dataset (:obj:`QGPODataset`): The dataset.
            model (:obj:`Union[torch.nn.Module, torch.nn.ModuleDict]`): The model.
        Interface:
            ``__init__``, ``train``, ``deploy``
        """
        self.config = config
        self.simulator = simulator
        self.dataset = dataset

        # ---------------------------------------
        # Customized model initialization code ↓
        # ---------------------------------------

        self.model = model if model is not None else torch.nn.ModuleDict()
        
        if model is not None:
            self.model = model
            self.behaviour_train_epoch = 0
            self.critic_train_epoch = 0
        else:
            self.model = torch.nn.ModuleDict()
            config = self.config.train
            assert hasattr(config.model, "IDQLPolicy")

            if torch.__version__ >= "2.0.0":
                self.model["IDQLPolicy"] = torch.compile(
                    IDQLPolicy(config.model.IDQLPolicy).to(config.model.IDQLPolicy.device)
                )
            else:
                self.model["IDQLPolicy"] = IDQLPolicy(config.model.IDQLPolicy).to(
                    config.model.IDQLPolicy.device
                )

            if (
                hasattr(config.parameter, "checkpoint_path")
                and config.parameter.checkpoint_path is not None
            ):
                self.behaviour_train_epoch = load_model(
                    path=config.parameter.checkpoint_path,
                    model=self.model["IDQLPolicy"].diffusion_model.model,
                    optimizer=None,
                    prefix="behaviour_policy",
                )
                
                self.critic_train_epoch = load_model(
                    path=config.parameter.checkpoint_path,
                    model=self.model["IDQLPolicy"].critic,
                    optimizer=None,
                    prefix="critic",
                )

            else:
                self.behaviour_policy_train_epoch = 0
                self.critic_train_epoch = 0

        # ---------------------------------------
        # Customized model initialization code ↑
        # ---------------------------------------

    def train(self, config: EasyDict = None):
        """
        Overview:
            Train the model using the given configuration. \
            A weight-and-bias run will be created automatically when this function is called.
        Arguments:
            config (:obj:`EasyDict`): The training configuration.
        """
        set_seed(self.config.deploy.env["seed"])

        config = (
            merge_two_dicts_into_newone(
                self.config.train if hasattr(self.config, "train") else EasyDict(),
                config,
            )
            if config is not None
            else self.config.train
        )
        
        with wandb.init(
            project=(
                config.project if hasattr(config, "project") else __class__.__name__
            ),
            **config.wandb if hasattr(config, "wandb") else {},
        ) as wandb_run:
            config = merge_two_dicts_into_newone(EasyDict(wandb_run.config), config)
            wandb_run.config.update(config)
            self.config.train = config

            self.simulator = (
                create_simulator(config.simulator)
                if hasattr(config, "simulator")
                else self.simulator
            )
            self.dataset = (
                create_dataset(config.dataset)
                if hasattr(config, "dataset")
                else self.dataset
            )
            
            def evaluate(model, train_epoch,repeat=1):
                evaluation_results = dict()

                def policy(obs: np.ndarray) -> np.ndarray:
                    if isinstance(obs, np.ndarray):
                        obs = torch.tensor(
                            obs,
                            dtype=torch.float32,
                            device=config.model.IDQLPolicy.device,
                        ).unsqueeze(0)
                    elif isinstance(obs, dict):
                        for key in obs:
                            obs[key] = torch.tensor(
                                obs[key],
                                dtype=torch.float32,
                                device=config.model.IDQLPolicy.device
                            ).unsqueeze(0)
                            if obs[key].dim() == 1 and obs[key].shape[0] == 1:
                                obs[key] = obs[key].unsqueeze(1)
                        obs = TensorDict(obs, batch_size=[1])
                
                    action = (
                        model.get_action(state=obs)
                        .squeeze(0)
                        .cpu()
                        .detach()
                        .numpy()
                    )
                    return action

                eval_results = self.simulator.evaluate(
                    policy=policy, num_episodes=repeat
                )
                return_results = [
                    eval_results[i]["total_return"] for i in range(repeat)
                ]
                log.info(f"Return: {return_results}")
                return_mean = np.mean(return_results)
                return_std = np.std(return_results)
                return_max = np.max(return_results)
                return_min = np.min(return_results)
                evaluation_results[f"evaluation/return_mean"] = return_mean
                evaluation_results[f"evaluation/return_std"] = return_std
                evaluation_results[f"evaluation/return_max"] = return_max
                evaluation_results[f"evaluation/return_min"] = return_min
                
                if repeat > 1:
                    log.info(
                        f"Train epoch: {train_epoch}, return_mean: {return_mean}, return_std: {return_std}, return_max: {return_max}, return_min: {return_min}"
                    )
                else:
                    log.info(f"Train epoch: {train_epoch}, return: {return_mean}")

                return evaluation_results
            # ---------------------------------------
            # Customized training code ↓
            # ---------------------------------------
            
            replay_buffer=TensorDictReplayBuffer(
                storage=self.dataset.storage,
                batch_size=config.parameter.critic.batch_size,
                sampler=SamplerWithoutReplacement(),
                prefetch=10,
                pin_memory=True,
            )

            q_optimizer = torch.optim.Adam(
                self.model["IDQLPolicy"].critic.q0.parameters(),
                lr=config.parameter.critic.learning_rate,
            )
            v_optimizer = torch.optim.Adam(
                self.model["IDQLPolicy"].critic.vf.parameters(),
                lr=config.parameter.critic.learning_rate,
            )

            for epoch in track(
                range(config.parameter.critic.iterations),
                description="Critic training",
            ):
                counter = 0
                q_sum=0.0
                q_loss_sum = 0.0
                v_loss_sum = 0.0
                if self.critic_train_epoch >= epoch:
                    continue
                
                for index, data in enumerate(replay_buffer):
                    v_loss, next_v = self.model["IDQLPolicy"].v_loss(
                        state=data["s"].to(config.model.IDQLPolicy.device),
                        action=data["a"].to(config.model.IDQLPolicy.device),
                        next_state=data["s_"].to(config.model.IDQLPolicy.device),
                        tau=config.parameter.critic.tau,
                    )
                    v_optimizer.zero_grad(set_to_none=True)
                    v_loss.backward()
                    v_optimizer.step()

                    q_loss,q_mean,q_target = self.model["IDQLPolicy"].q_loss(
                        state=data["s"].to(config.model.IDQLPolicy.device),
                        action=data["a"].to(config.model.IDQLPolicy.device),
                        reward=data["r"].to(config.model.IDQLPolicy.device),
                        done=data["d"].to(config.model.IDQLPolicy.device),
                        next_v=next_v,
                        discount=config.parameter.critic.discount_factor,
                    )
                    
                    q_optimizer.zero_grad(set_to_none=True)
                    q_loss.backward()
                    q_optimizer.step()
                    
                    counter += 1
                    q_loss_sum += q_loss.item()
                    v_loss_sum += v_loss.item()
                    q_sum +=q_mean.item()
            
                    # Update target
                    for param, target_param in zip(
                        self.model["IDQLPolicy"].critic.q0.parameters(),
                        self.model["IDQLPolicy"].critic.q0_target.parameters(),
                    ):
                        target_param.data.copy_(
                            config.parameter.critic.update_momentum * param.data
                            + (1 - config.parameter.critic.update_momentum) * target_param.data
                        )

                wandb.log(
                    data=dict(
                        critic_train_epoch=epoch,
                        q_loss=q_loss_sum / counter,
                        v_loss=v_loss_sum / counter,
                        q=q_sum / counter,
                    ),
                    commit=True,
                )
                if (
                    hasattr(config.parameter.critic, "checkpoint_freq")
                    and (epoch + 1) % config.parameter.critic.checkpoint_freq == 0
                ):
                    save_model(
                        path=config.parameter.checkpoint_path,
                        model=self.model["IDQLPolicy"].critic,
                        optimizer=q_optimizer,
                        iteration=epoch,
                        prefix="critic",
                    )


            replay_buffer=TensorDictReplayBuffer(
                storage=self.dataset.storage,
                batch_size=config.parameter.behaviour_policy.batch_size,
                sampler=SamplerWithoutReplacement(),
                prefetch=10,
                pin_memory=True,
            )
            
            behaviour_model_optimizer = torch.optim.Adam(
                self.model["IDQLPolicy"].diffusion_model.model.parameters(),
                lr=config.parameter.behaviour_policy.learning_rate,
            )

            for epoch in track(
                range(config.parameter.behaviour_policy.iterations),
                description="Behaviour policy training",
            ):
                if self.behaviour_train_epoch >= epoch:
                    continue
                
                counter=0
                behaviour_model_training_loss_sum = 0.0
                for index, data in enumerate(replay_buffer):
                    behaviour_model_training_loss = self.model[
                        "IDQLPolicy"
                    ].behaviour_policy_loss(data["a"].to(config.model.IDQLPolicy.device), data["s"].to(config.model.IDQLPolicy.device))
                    behaviour_model_optimizer.zero_grad()
                    behaviour_model_training_loss.backward()
                    behaviour_model_optimizer.step()
                    counter += 1
                    behaviour_model_training_loss_sum += behaviour_model_training_loss.item()
            
                self.behaviour_policy_train_epoch = epoch
                
                if (
                    epoch == 0
                    or (epoch + 1)
                    % config.parameter.evaluation.evaluation_interval
                    == 0
                ):
                    evaluation_results = evaluate(
                        self.model["IDQLPolicy"], train_epoch=epoch,
                        repeat=(
                            1
                            if not hasattr(config.parameter.evaluation, "repeat")
                            else config.parameter.evaluation.repeat
                        ),
                    )
                    wandb_run.log(data=evaluation_results, commit=False)
                    save_model(
                        path=config.parameter.checkpoint_path,
                        model=self.model["IDQLPolicy"].diffusion_model.model,
                        optimizer=behaviour_model_optimizer,
                        iteration=epoch,
                        prefix="behaviour_policy",
                    )
                    
                wandb_run.log(
                    data=dict(
                        behaviour_policy_train_epoch=epoch,
                        behaviour_model_training_loss=behaviour_model_training_loss_sum / counter,
                    ),
                    commit=True,
                )
                
            
            # ---------------------------------------
            # Customized training code ↑
            # ---------------------------------------

            wandb.finish()

    def deploy(self, config: EasyDict = None) -> SRPOAgent:
        """
        Overview:
            Deploy the model using the given configuration.
        Arguments:
            config (:obj:`EasyDict`): The deployment configuration.
        """

        if config is not None:
            config = merge_two_dicts_into_newone(self.config.deploy, config)
        else:
            config = self.config.deploy

        return SRPOAgent(
            config=config,
            model=torch.nn.ModuleDict(
                {
                    "IDQLPolicy": self.deter_policy.select_actions,
                }
            ),
        )
