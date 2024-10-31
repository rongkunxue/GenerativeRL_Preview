from typing import List, Tuple, Union

import torch
import treetensor
from easydict import EasyDict
from tensordict import TensorDict

from grl.generative_models.model_functions.velocity_function import VelocityFunction
from grl.generative_models.random_generator import gaussian_random_variable
from grl.generative_models.stochastic_process import StochasticProcess
from grl.numerical_methods.numerical_solvers import get_solver
from grl.numerical_methods.numerical_solvers.dpm_solver import DPMSolver
from grl.numerical_methods.numerical_solvers.ode_solver import (
    DictTensorODESolver,
    ODESolver,
)
from grl.numerical_methods.numerical_solvers.sde_solver import SDESolver
from grl.numerical_methods.probability_path import (
    ConditionalProbabilityPath,
)
from grl.utils import find_parameters


class GuidedConditionalFlowModel:
    """
    Overview:
        The guided conditional flow model, which is used for sampling from a larger guidance scale than the guided model by assigning a guidance scale larger than 1.
    Interfaces:
        ``__init__``, ``get_type``, ``sample``, ``sample_forward_process``
    """

    def __init__(
        self,
        config: EasyDict,
    ):
        """
        Overview:
            Initialize the model.
        Arguments:
            config (:obj:`EasyDict`): The configuration.
        """
        super().__init__()
        self.config = config

        self.x_size = config.x_size
        self.device = config.device

        self.gaussian_generator = gaussian_random_variable(
            config.x_size,
            config.device,
            config.use_tree_tensor if hasattr(config, "use_tree_tensor") else False,
        )

        self.path = ConditionalProbabilityPath(config.path)
        if hasattr(config, "reverse_path"):
            self.reverse_path = ConditionalProbabilityPath(config.reverse_path)
        else:
            self.reverse_path = None
        self.model_type = config.model.type
        assert self.model_type in [
            "velocity_function",
        ], "Unknown type of model {}".format(self.model_type)

        self.diffusion_process = StochasticProcess(self.path)
        self.velocity_function_ = VelocityFunction(
            self.model_type, self.diffusion_process
        )

        if hasattr(config, "solver"):
            self.solver = get_solver(config.solver.type)(**config.solver.args)

    def get_type(self):
        return "GuidedConditionalFlowModel"

    def sample(
        self,
        base_model,
        guided_model,
        t_span: torch.Tensor = None,
        batch_size: Union[torch.Size, int, Tuple[int], List[int]] = None,
        x_0: Union[torch.Tensor, TensorDict, treetensor.torch.Tensor] = None,
        condition: Union[torch.Tensor, TensorDict, treetensor.torch.Tensor] = None,
        with_grad: bool = False,
        solver_config: EasyDict = None,
        guidance_scale: float = 1.0,
    ):
        """
        Overview:
            Sample from the model, return the final state.

        Arguments:
            base_model (:obj:`nn.Module`): The base model.
            guided_model (:obj:`nn.Module`): The guided model.
            t_span (:obj:`torch.Tensor`): The time span.
            batch_size (:obj:`Union[torch.Size, int, Tuple[int], List[int]]`): The batch size.
            x_0 (:obj:`Union[torch.Tensor, TensorDict, treetensor.torch.Tensor]`): The initial state, if not provided, it will be sampled from the Gaussian distribution.
            condition (:obj:`Union[torch.Tensor, TensorDict, treetensor.torch.Tensor]`): The input condition.
            with_grad (:obj:`bool`): Whether to return the gradient.
            solver_config (:obj:`EasyDict`): The configuration of the solver.

        Returns:
            x (:obj:`Union[torch.Tensor, TensorDict, treetensor.torch.Tensor]`): The sampled result.

        Shapes:
            t_span: :math:`(T)`, where :math:`T` is the number of time steps.
            batch_size: :math:`(B)`, where :math:`B` is the batch size of data, which could be a scalar or a tensor such as :math:`(B1, B2)`.
            x_0: :math:`(N, D)`, where :math:`N` is the batch size of data and :math:`D` is the dimension of the state, which could be a scalar or a tensor such as :math:`(D1, D2)`.
            condition: :math:`(N, D)`, where :math:`N` is the batch size of data and :math:`D` is the dimension of the condition, which could be a scalar or a tensor such as :math:`(D1, D2)`.
            x: :math:`(N, D)`, if extra batch size :math:`B` is provided, the shape will be :math:`(B, N, D)`. If x_0 is not provided, the shape will be :math:`(B, D)`. If x_0 and condition are not provided, the shape will be :math:`(D)`.
        """

        return self.sample_forward_process(
            base_model=base_model,
            guided_model=guided_model,
            t_span=t_span,
            batch_size=batch_size,
            x_0=x_0,
            condition=condition,
            with_grad=with_grad,
            solver_config=solver_config,
            guidance_scale=guidance_scale,
        )[-1]

    def sample_forward_process(
        self,
        base_model,
        guided_model,
        t_span: torch.Tensor = None,
        batch_size: Union[torch.Size, int, Tuple[int], List[int]] = None,
        x_0: Union[torch.Tensor, TensorDict, treetensor.torch.Tensor] = None,
        condition: Union[torch.Tensor, TensorDict, treetensor.torch.Tensor] = None,
        with_grad: bool = False,
        solver_config: EasyDict = None,
        guidance_scale: float = 1.0,
    ):
        """
        Overview:
            Sample from the model, return all intermediate states.

        Arguments:
            base_model (:obj:`nn.Module`): The base model.
            guided_model (:obj:`nn.Module`): The guided model.
            t_span (:obj:`torch.Tensor`): The time span.
            batch_size (:obj:`Union[torch.Size, int, Tuple[int], List[int]]`): An extra batch size used for repeated sampling with the same initial state.
            x_0 (:obj:`Union[torch.Tensor, TensorDict, treetensor.torch.Tensor]`): The initial state, if not provided, it will be sampled from the Gaussian distribution.
            condition (:obj:`Union[torch.Tensor, TensorDict, treetensor.torch.Tensor]`): The input condition.
            with_grad (:obj:`bool`): Whether to return the gradient.
            solver_config (:obj:`EasyDict`): The configuration of the solver.

        Returns:
            x (:obj:`Union[torch.Tensor, TensorDict, treetensor.torch.Tensor]`): The sampled result.

        Shapes:
            t_span: :math:`(T)`, where :math:`T` is the number of time steps.
            batch_size: :math:`(B)`, where :math:`B` is the batch size of data, which could be a scalar or a tensor such as :math:`(B1, B2)`.
            x_0: :math:`(N, D)`, where :math:`N` is the batch size of data and :math:`D` is the dimension of the state, which could be a scalar or a tensor such as :math:`(D1, D2)`.
            condition: :math:`(N, D)`, where :math:`N` is the batch size of data and :math:`D` is the dimension of the condition, which could be a scalar or a tensor such as :math:`(D1, D2)`.
            x: :math:`(T, N, D)`, if extra batch size :math:`B` is provided, the shape will be :math:`(T, B, N, D)`. If x_0 is not provided, the shape will be :math:`(T, B, D)`. If x_0 and condition are not provided, the shape will be :math:`(T, D)`.
        """

        if t_span is not None:
            t_span = t_span.to(self.device)

        if batch_size is None:
            extra_batch_size = torch.tensor((1,), device=self.device)
        elif isinstance(batch_size, int):
            extra_batch_size = torch.tensor((batch_size,), device=self.device)
        else:
            if (
                isinstance(batch_size, torch.Size)
                or isinstance(batch_size, Tuple)
                or isinstance(batch_size, List)
            ):
                extra_batch_size = torch.tensor(batch_size, device=self.device)
            else:
                assert False, "Invalid batch size"

        if x_0 is not None and condition is not None:
            assert (
                x_0.shape[0] == condition.shape[0]
            ), "The batch size of x_0 and condition must be the same"
            data_batch_size = x_0.shape[0]
        elif x_0 is not None:
            data_batch_size = x_0.shape[0]
        elif condition is not None:
            data_batch_size = condition.shape[0]
        else:
            data_batch_size = 1

        if solver_config is not None:
            solver = get_solver(solver_config.type)(**solver_config.args)
        else:
            assert hasattr(
                self, "solver"
            ), "solver must be specified in config or solver_config"
            solver = self.solver

        if x_0 is None:
            x = self.gaussian_generator(
                batch_size=torch.prod(extra_batch_size) * data_batch_size
            )
            # x.shape = (B*N, D)
        else:
            if isinstance(self.x_size, int):
                assert (
                    torch.Size([self.x_size]) == x_0[0].shape
                ), "The shape of x_0 must be the same as the x_size that is specified in the config"
            elif (
                isinstance(self.x_size, Tuple)
                or isinstance(self.x_size, List)
                or isinstance(self.x_size, torch.Size)
            ):
                assert (
                    torch.Size(self.x_size) == x_0[0].shape
                ), "The shape of x_0 must be the same as the x_size that is specified in the config"
            else:
                assert False, "Invalid x_size"

            x = torch.repeat_interleave(x_0, torch.prod(extra_batch_size), dim=0)
            # x.shape = (B*N, D)

        if condition is not None:
            if isinstance(condition, torch.Tensor):
                condition = torch.repeat_interleave(
                    condition, torch.prod(extra_batch_size), dim=0
                )
                # condition.shape = (B*N, D)
            elif isinstance(condition, TensorDict):
                condition = TensorDict(
                    {
                        key: torch.repeat_interleave(
                            condition[key], torch.prod(extra_batch_size), dim=0
                        )
                        for key in condition.keys()
                    },
                    batch_size=torch.prod(extra_batch_size) * condition.shape,
                    device=condition.device,
                )
            else:
                raise NotImplementedError("Not implemented")

        if isinstance(solver, DPMSolver):
            raise NotImplementedError("Not implemented")
        elif isinstance(solver, ODESolver):
            # TODO: make it compatible with TensorDict
            def drift(t, x):
                return (1.0 - guidance_scale) * base_model.model(
                    t=t, x=x, condition=condition
                ) + guidance_scale * guided_model.model(t=t, x=x, condition=condition)

            if solver.library == "torchdiffeq_adjoint":
                if with_grad:
                    data = solver.integrate(
                        drift=drift,
                        x0=x,
                        t_span=t_span,
                        adjoint_params=find_parameters(base_model)
                        + find_parameters(guided_model),
                    )
                else:
                    with torch.no_grad():
                        data = solver.integrate(
                            drift=drift,
                            x0=x,
                            t_span=t_span,
                            adjoint_params=find_parameters(base_model)
                            + find_parameters(guided_model),
                        )
            else:
                if with_grad:
                    data = solver.integrate(
                        drift=drift,
                        x0=x,
                        t_span=t_span,
                    )
                else:
                    with torch.no_grad():
                        data = solver.integrate(
                            drift=drift,
                            x0=x,
                            t_span=t_span,
                        )
        elif isinstance(solver, DictTensorODESolver):
            # TODO: make it compatible with TensorDict
            def drift(t, x):
                return (1.0 - guidance_scale) * base_model.model(
                    t=t, x=x, condition=condition
                ) + guidance_scale * guided_model.model(t=t, x=x, condition=condition)

            if with_grad:
                data = solver.integrate(
                    drift=drift,
                    x0=x,
                    t_span=t_span,
                    batch_size=torch.prod(extra_batch_size) * data_batch_size,
                    x_size=x.shape,
                )
            else:
                with torch.no_grad():
                    data = solver.integrate(
                        drift=drift,
                        x0=x,
                        t_span=t_span,
                        batch_size=torch.prod(extra_batch_size) * data_batch_size,
                        x_size=x.shape,
                    )
        elif isinstance(solver, SDESolver):
            raise NotImplementedError("Not implemented")
        else:
            raise NotImplementedError(
                "Solver type {} is not implemented".format(self.config.solver.type)
            )

        if isinstance(data, torch.Tensor):
            # data.shape = (T, B*N, D)
            if len(extra_batch_size.shape) == 0:
                if isinstance(self.x_size, int):
                    data = data.reshape(
                        -1, extra_batch_size, data_batch_size, self.x_size
                    )
                elif (
                    isinstance(self.x_size, Tuple)
                    or isinstance(self.x_size, List)
                    or isinstance(self.x_size, torch.Size)
                ):
                    data = data.reshape(
                        -1, extra_batch_size, data_batch_size, *self.x_size
                    )
                else:
                    assert False, "Invalid x_size"
            else:
                if isinstance(self.x_size, int):
                    data = data.reshape(
                        -1, *extra_batch_size, data_batch_size, self.x_size
                    )
                elif (
                    isinstance(self.x_size, Tuple)
                    or isinstance(self.x_size, List)
                    or isinstance(self.x_size, torch.Size)
                ):
                    data = data.reshape(
                        -1, *extra_batch_size, data_batch_size, *self.x_size
                    )
                else:
                    assert False, "Invalid x_size"
            # data.shape = (T, B, N, D)

            if batch_size is None:
                if x_0 is None and condition is None:
                    data = data.squeeze(1).squeeze(1)
                    # data.shape = (T, D)
                else:
                    data = data.squeeze(1)
                    # data.shape = (T, N, D)
            else:
                if x_0 is None and condition is None:
                    data = data.squeeze(1 + len(extra_batch_size.shape))
                    # data.shape = (T, B, D)
                else:
                    # data.shape = (T, B, N, D)
                    pass
        elif isinstance(data, TensorDict):
            raise NotImplementedError("Not implemented")
        elif isinstance(data, treetensor.torch.Tensor):
            for key in data.keys():
                if len(extra_batch_size.shape) == 0:
                    if isinstance(self.x_size[key], int):
                        data[key] = data[key].reshape(
                            -1, extra_batch_size, data_batch_size, self.x_size[key]
                        )
                    elif (
                        isinstance(self.x_size[key], Tuple)
                        or isinstance(self.x_size[key], List)
                        or isinstance(self.x_size[key], torch.Size)
                    ):
                        data[key] = data[key].reshape(
                            -1, extra_batch_size, data_batch_size, *self.x_size[key]
                        )
                    else:
                        assert False, "Invalid x_size"
                else:
                    if isinstance(self.x_size[key], int):
                        data[key] = data[key].reshape(
                            -1, *extra_batch_size, data_batch_size, self.x_size[key]
                        )
                    elif (
                        isinstance(self.x_size[key], Tuple)
                        or isinstance(self.x_size[key], List)
                        or isinstance(self.x_size[key], torch.Size)
                    ):
                        data[key] = data[key].reshape(
                            -1, *extra_batch_size, data_batch_size, *self.x_size[key]
                        )
                    else:
                        assert False, "Invalid x_size"
                # data.shape = (T, B, N, D)

                if batch_size is None:
                    if x_0 is None and condition is None:
                        data[key] = data[key].squeeze(1).squeeze(1)
                        # data.shape = (T, D)
                    else:
                        data[key] = data[key].squeeze(1)
                        # data.shape = (T, N, D)
                else:
                    if x_0 is None and condition is None:
                        data[key] = data[key].squeeze(1 + len(extra_batch_size.shape))
                        # data.shape = (T, B, D)
                    else:
                        # data.shape = (T, B, N, D)
                        pass
        else:
            raise NotImplementedError("Not implemented")

        return data
