from abc import abstractmethod
from typing import List

import gym
import numpy as np
import torch
from tensordict import TensorDict

from torchrl.data import LazyTensorStorage
from grl.utils.log import log


class GPDataset(torch.utils.data.Dataset):
    """
    Overview:
        Dataset for Generative Policy algorithm.
        The training of Generative Policy algorithm sometimes needs true action and fake action.
        The true action is sampled from the dataset, and the fake action \
        is sampled from the behaviour policy, which is data augmentation.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(self):
        """
        Overview:
            Initialization method of GPD4RLDataset class
        """
        pass

    def __getitem__(self, index):
        """
        Overview:
            Get data by index
        Arguments:
            index (:obj:`int`): Index of data
        Returns:
            data (:obj:`dict`): Data dict
        
        .. note::
            The data dict contains the following keys:
            
            s (:obj:`torch.Tensor`): State
            a (:obj:`torch.Tensor`): Action
            r (:obj:`torch.Tensor`): Reward
            s_ (:obj:`torch.Tensor`): Next state
            d (:obj:`torch.Tensor`): Is finished
            fake_a (:obj:`torch.Tensor`): Fake action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
            fake_a_ (:obj:`torch.Tensor`): Fake next action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
        """

        data = {
            "s": self.states[index % self.len],
            "a": self.actions[index % self.len],
            "r": self.rewards[index % self.len],
            "s_": self.next_states[index % self.len],
            "d": self.is_finished[index % self.len],
            "fake_a": (
                self.fake_actions[index % self.len]
                if hasattr(self, "fake_actions")
                else 0.0
            ),  # self.fake_actions <D, 16, A>
            "fake_a_": (
                self.fake_next_actions[index % self.len]
                if hasattr(self, "fake_next_actions")
                else 0.0
            ),  # self.fake_next_actions <D, 16, A>
        }
        return data

    def __len__(self):
        return self.len

    def load_fake_actions(self, fake_actions, fake_next_actions):
        self.fake_actions = fake_actions
        self.fake_next_actions = fake_next_actions

    @abstractmethod
    def return_range(self, dataset, max_episode_steps):
        raise NotImplementedError

class GPTensorDictDataset(torch.utils.data.Dataset):
    """
    Overview:
        Dataset for Generative Policy algorithm.
        The training of Generative Policy algorithm sometimes needs true action and fake action.
        The true action is sampled from the dataset, and the fake action \
        is sampled from the behaviour policy, which is data augmentation.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(self):
        """
        Overview:
            Initialization method of GPD4RLDataset class
        """
        pass

    def __getitem__(self, index):
        """
        Overview:
            Get data by index
        Arguments:
            index (:obj:`int`): Index of data
        Returns:
            data (:obj:`dict`): Data dict
        
        .. note::
            The data dict contains the following keys:
            
            s (:obj:`torch.Tensor`): State
            a (:obj:`torch.Tensor`): Action
            r (:obj:`torch.Tensor`): Reward
            s_ (:obj:`torch.Tensor`): Next state
            d (:obj:`torch.Tensor`): Is finished
            fake_a (:obj:`torch.Tensor`): Fake action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
            fake_a_ (:obj:`torch.Tensor`): Fake next action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
        """

        data = self.storage.get(index=index)
        return data

    def __len__(self):
        return self.len

    def load_fake_actions(self, fake_actions, fake_next_actions):
        self.fake_actions = fake_actions
        self.fake_next_actions = fake_next_actions
        if self.action_augment_num:
            self.storage.set(
                range(self.len), TensorDict(
                    {
                        "s": self.states,
                        "a": self.actions,
                        "r": self.rewards,
                        "s_": self.next_states,
                        "d": self.is_finished,
                        "fake_a": self.fake_actions,
                        "fake_a_": self.fake_next_actions,
                    },
                    batch_size=[self.len],
                )
            )
        else:
            self.storage.set(
                range(self.len), TensorDict(
                    {
                        "s": self.states,
                        "a": self.actions,
                        "r": self.rewards,
                        "s_": self.next_states,
                        "d": self.is_finished,
                    },
                    batch_size=[self.len],
                )
            )

    @abstractmethod
    def return_range(self, dataset, max_episode_steps):
        raise NotImplementedError


class GPD4RLDataset(GPDataset):
    """
    Overview:
        D4RL Dataset for Generative Policy algorithm.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        env_id: str,
    ):
        """
        Overview:
            Initialization method of GPD4RLDataset class
        Arguments:
            env_id (:obj:`str`): The environment id
        """

        super().__init__()
        import d4rl

        data = d4rl.qlearning_dataset(gym.make(env_id))
        self.states = torch.from_numpy(data["observations"]).float()
        self.actions = torch.from_numpy(data["actions"]).float()
        self.next_states = torch.from_numpy(data["next_observations"]).float()
        reward = torch.from_numpy(data["rewards"]).view(-1, 1).float()
        self.is_finished = torch.from_numpy(data["terminals"]).view(-1, 1).float()

        reward_tune = "iql_antmaze" if "antmaze" in env_id else "iql_locomotion"
        if reward_tune == "normalize":
            reward = (reward - reward.mean()) / reward.std()
        elif reward_tune == "iql_antmaze":
            reward = reward - 1.0
        elif reward_tune == "iql_locomotion":
            min_ret, max_ret = GPD4RLDataset.return_range(data, 1000)
            reward /= max_ret - min_ret
            reward *= 1000
        elif reward_tune == "cql_antmaze":
            reward = (reward - 0.5) * 4.0
        elif reward_tune == "antmaze":
            reward = (reward - 0.25) * 2.0
        self.rewards = reward
        self.len = self.states.shape[0]
        log.info(f"{self.len} data loaded in GPD4RLDataset")

    def return_range(dataset, max_episode_steps):
        returns, lengths = [], []
        ep_ret, ep_len = 0.0, 0
        for r, d in zip(dataset["rewards"], dataset["terminals"]):
            ep_ret += float(r)
            ep_len += 1
            if d or ep_len == max_episode_steps:
                returns.append(ep_ret)
                lengths.append(ep_len)
                ep_ret, ep_len = 0.0, 0
        # returns.append(ep_ret)    # incomplete trajectory
        lengths.append(ep_len)  # but still keep track of number of steps
        assert sum(lengths) == len(dataset["rewards"])
        return min(returns), max(returns)


class GPOnlineDataset(GPDataset):
    """
    Overview:
        Dataset for Generative Policy algorithm for online data collection.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        fake_action_shape: int = None,
        data: List = None,
    ):
        """
        Overview:
            Initialization method of GPD4RLDataset class
        Arguments:
            data (:obj:`List`): The data list
        """

        super().__init__()
        self.fake_action_shape = fake_action_shape
        if data is not None:
            self.states = torch.from_numpy(data["observations"]).float()
            self.actions = torch.from_numpy(data["actions"]).float()
            self.next_states = torch.from_numpy(data["next_observations"]).float()
            reward = torch.from_numpy(data["rewards"]).view(-1, 1).float()
            self.is_finished = torch.from_numpy(data["terminals"]).view(-1, 1).float()
            self.rewards = reward
            # self.fake_actions = torch.zeros_like(self.actions.unsqueeze(1).expand(-1, fake_action_shape, -1))
            # self.fake_next_actions = torch.zeros_like(self.actions.unsqueeze(1).expand(-1, fake_action_shape, -1))
            self.len = self.states.shape[0]
        else:
            self.states = torch.tensor([])
            self.actions = torch.tensor([])
            self.next_states = torch.tensor([])
            self.is_finished = torch.tensor([])
            self.rewards = torch.tensor([])
            # self.fake_actions = torch.tensor([])
            # self.fake_next_actions = torch.tensor([])
            self.len = 0
        log.debug(f"{self.len} data loaded in GPOnlineDataset")

    def drop_data(self, drop_ratio: float, random: bool = True):
        # drop the data from the dataset
        drop_num = int(self.len * drop_ratio)
        # randomly drop the data if random is True
        if random:
            drop_indices = torch.randperm(self.len)[:drop_num]
        else:
            drop_indices = torch.arange(drop_num)
        keep_mask = torch.ones(self.len, dtype=torch.bool)
        keep_mask[drop_indices] = False
        self.states = self.states[keep_mask]
        self.actions = self.actions[keep_mask]
        self.next_states = self.next_states[keep_mask]
        self.is_finished = self.is_finished[keep_mask]
        self.rewards = self.rewards[keep_mask]
        # self.fake_actions = self.fake_actions[keep_mask]
        # self.fake_next_actions = self.fake_next_actions[keep_mask]
        self.len = self.states.shape[0]
        log.debug(f"{drop_num} data dropped in GPOnlineDataset")

    def load_data(self, data: List):
        # concatenate the data into the dataset

        # collate the data by sorting the keys

        keys = ["obs", "action", "done", "next_obs", "reward"]

        collated_data = {
            k: torch.tensor(np.stack([item[k] for item in data]))
            for i, k in enumerate(keys)
        }

        self.states = torch.cat([self.states, collated_data["obs"].float()], dim=0)
        self.actions = torch.cat([self.actions, collated_data["action"].float()], dim=0)
        self.next_states = torch.cat(
            [self.next_states, collated_data["next_obs"].float()], dim=0
        )
        reward = collated_data["reward"].view(-1, 1).float()
        self.is_finished = torch.cat(
            [self.is_finished, collated_data["done"].view(-1, 1).float()], dim=0
        )
        self.rewards = torch.cat([self.rewards, reward], dim=0)
        # self.fake_actions = torch.cat([self.fake_actions, torch.zeros_like(collated_data['action'].unsqueeze(1).expand(-1, self.fake_action_shape, -1))], dim=0)
        # self.fake_next_actions = torch.cat([self.fake_next_actions, torch.zeros_like(collated_data['action'].unsqueeze(1).expand(-1, self.fake_action_shape, -1))], dim=0)
        self.len = self.states.shape[0]
        log.debug(f"{self.len} data loaded in GPOnlineDataset")

    def return_range(dataset, max_episode_steps):
        returns, lengths = [], []
        ep_ret, ep_len = 0.0, 0
        for r, d in zip(dataset["rewards"], dataset["terminals"]):
            ep_ret += float(r)
            ep_len += 1
            if d or ep_len == max_episode_steps:
                returns.append(ep_ret)
                lengths.append(ep_len)
                ep_ret, ep_len = 0.0, 0
        # returns.append(ep_ret)    # incomplete trajectory
        lengths.append(ep_len)  # but still keep track of number of steps
        assert sum(lengths) == len(dataset["rewards"])
        return min(returns), max(returns)


class GPD4RLOnlineDataset(GPDataset):
    """
    Overview:
        D4RL Dataset for GP algorithm for online data collection.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        env_id: str,
        fake_action_shape: int = None,
    ):
        """
        Overview:
            Initialization method of GPD4RLDataset class
        Arguments:
            data (:obj:`List`): The data list
        """

        super().__init__()
        self.fake_action_shape = fake_action_shape
        import d4rl

        data = d4rl.qlearning_dataset(gym.make(env_id))
        self.states = torch.from_numpy(data["observations"]).float()
        self.actions = torch.from_numpy(data["actions"]).float()
        self.next_states = torch.from_numpy(data["next_observations"]).float()
        reward = torch.from_numpy(data["rewards"]).view(-1, 1).float()
        self.is_finished = torch.from_numpy(data["terminals"]).view(-1, 1).float()

        reward_tune = "iql_antmaze" if "antmaze" in env_id else "iql_locomotion"
        if reward_tune == "normalize":
            reward = (reward - reward.mean()) / reward.std()
        elif reward_tune == "iql_antmaze":
            reward = reward - 1.0
        elif reward_tune == "iql_locomotion":
            min_ret, max_ret = GPD4RLDataset.return_range(data, 1000)
            reward /= max_ret - min_ret
            reward *= 1000
        elif reward_tune == "cql_antmaze":
            reward = (reward - 0.5) * 4.0
        elif reward_tune == "antmaze":
            reward = (reward - 0.25) * 2.0
        self.rewards = reward
        self.len = self.states.shape[0]

        log.debug(f"{self.len} data loaded in GPD4RLOnlineDataset")

    def drop_data(self, drop_ratio: float, random: bool = True):
        # drop the data from the dataset
        drop_num = int(self.len * drop_ratio)
        # randomly drop the data if random is True
        if random:
            drop_indices = torch.randperm(self.len)[:drop_num]
        else:
            drop_indices = torch.arange(drop_num)
        keep_mask = torch.ones(self.len, dtype=torch.bool)
        keep_mask[drop_indices] = False
        self.states = self.states[keep_mask]
        self.actions = self.actions[keep_mask]
        self.next_states = self.next_states[keep_mask]
        self.is_finished = self.is_finished[keep_mask]
        self.rewards = self.rewards[keep_mask]
        # self.fake_actions = self.fake_actions[keep_mask]
        # self.fake_next_actions = self.fake_next_actions[keep_mask]
        self.len = self.states.shape[0]
        log.debug(f"{drop_num} data dropped in GPOnlineDataset")

    def load_data(self, data: List):
        # concatenate the data into the dataset

        # collate the data by sorting the keys

        keys = ["obs", "action", "done", "next_obs", "reward"]

        collated_data = {
            k: torch.tensor(np.stack([item[k] for item in data]))
            for i, k in enumerate(keys)
        }

        self.states = torch.cat([self.states, collated_data["obs"].float()], dim=0)
        self.actions = torch.cat([self.actions, collated_data["action"].float()], dim=0)
        self.next_states = torch.cat(
            [self.next_states, collated_data["next_obs"].float()], dim=0
        )
        reward = collated_data["reward"].view(-1, 1).float()
        self.is_finished = torch.cat(
            [self.is_finished, collated_data["done"].view(-1, 1).float()], dim=0
        )
        self.rewards = torch.cat([self.rewards, reward], dim=0)
        # self.fake_actions = torch.cat([self.fake_actions, torch.zeros_like(collated_data['action'].unsqueeze(1).expand(-1, self.fake_action_shape, -1))], dim=0)
        # self.fake_next_actions = torch.cat([self.fake_next_actions, torch.zeros_like(collated_data['action'].unsqueeze(1).expand(-1, self.fake_action_shape, -1))], dim=0)
        self.len = self.states.shape[0]
        log.debug(f"{self.len} data loaded in GPOnlineDataset")

    def return_range(dataset, max_episode_steps):
        returns, lengths = [], []
        ep_ret, ep_len = 0.0, 0
        for r, d in zip(dataset["rewards"], dataset["terminals"]):
            ep_ret += float(r)
            ep_len += 1
            if d or ep_len == max_episode_steps:
                returns.append(ep_ret)
                lengths.append(ep_len)
                ep_ret, ep_len = 0.0, 0
        # returns.append(ep_ret)    # incomplete trajectory
        lengths.append(ep_len)  # but still keep track of number of steps
        assert sum(lengths) == len(dataset["rewards"])
        return min(returns), max(returns)


class GPCustomizedDataset(GPDataset):
    """
    Overview:
        Dataset for Generative Policy algorithm for customized data.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        env_id: str = None,
        numpy_data_path: str = None,
    ):
        """
        Overview:
            Initialization method of GPCustomizedDataset class
        Arguments:
            env_id (:obj:`str`): The environment id
            numpy_data_path (:obj:`str`): The path to the numpy data
        """

        super().__init__()

        data = np.load(numpy_data_path)

        self.states = torch.from_numpy(data["obs"]).float()
        self.actions = torch.from_numpy(data["action"]).float()
        self.next_states = torch.from_numpy(data["next_obs"]).float()
        reward = torch.from_numpy(data["reward"]).view(-1, 1).float()
        self.is_finished = torch.from_numpy(data["done"]).view(-1, 1).float()

        self.rewards = reward
        self.len = self.states.shape[0]
        log.info(f"{self.len} data loaded in GPCustomizedDataset")


class GPD4RLTensorDictDataset(GPTensorDictDataset):
    """
    Overview:
        D4RL Dataset for Generative Policy algorithm.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        env_id: str,
        action_augment_num: int = None,
    ):
        """
        Overview:
            Initialization method of GPD4RLDataset class
        Arguments:
            env_id (:obj:`str`): The environment id
        """

        super().__init__()
        import d4rl

        data = d4rl.qlearning_dataset(gym.make(env_id))
        self.states = torch.from_numpy(data["observations"]).float()
        self.actions = torch.from_numpy(data["actions"]).float()
        self.next_states = torch.from_numpy(data["next_observations"]).float()
        reward = torch.from_numpy(data["rewards"]).view(-1, 1).float()
        self.is_finished = torch.from_numpy(data["terminals"]).view(-1, 1).float()

        reward_tune = "iql_antmaze" if "antmaze" in env_id else "iql_locomotion"
        if reward_tune == "normalize":
            reward = (reward - reward.mean()) / reward.std()
        elif reward_tune == "iql_antmaze":
            reward = reward - 1.0
        elif reward_tune == "iql_locomotion":
            min_ret, max_ret = GPD4RLDataset.return_range(data, 1000)
            reward /= max_ret - min_ret
            reward *= 1000
        elif reward_tune == "cql_antmaze":
            reward = (reward - 0.5) * 4.0
        elif reward_tune == "antmaze":
            reward = (reward - 0.25) * 2.0
        self.rewards = reward
        self.len = self.states.shape[0]
        log.info(f"{self.len} data loaded in GPD4RLDataset")
        self.action_augment_num = action_augment_num
        self.storage = LazyTensorStorage(max_size=self.len)
        if self.action_augment_num:
            self.storage.set(
                range(self.len), TensorDict(
                    {
                        "s": self.states,
                        "a": self.actions,
                        "r": self.rewards,
                        "s_": self.next_states,
                        "d": self.is_finished,
                        "fake_a": torch.zeros_like(self.actions).unsqueeze(1).repeat_interleave(self.action_augment_num, dim=1),
                        "fake_a_": torch.zeros_like(self.actions).unsqueeze(1).repeat_interleave(self.action_augment_num, dim=1),
                    },
                    batch_size=[self.len],
                )
            )
        else:
            self.storage.set(
                range(self.len), TensorDict(
                    {
                        "s": self.states,
                        "a": self.actions,
                        "r": self.rewards,
                        "s_": self.next_states,
                        "d": self.is_finished,
                    },
                    batch_size=[self.len],
                )
            )

    def return_range(dataset, max_episode_steps):
        returns, lengths = [], []
        ep_ret, ep_len = 0.0, 0
        for r, d in zip(dataset["rewards"], dataset["terminals"]):
            ep_ret += float(r)
            ep_len += 1
            if d or ep_len == max_episode_steps:
                returns.append(ep_ret)
                lengths.append(ep_len)
                ep_ret, ep_len = 0.0, 0
        # returns.append(ep_ret)    # incomplete trajectory
        lengths.append(ep_len)  # but still keep track of number of steps
        assert sum(lengths) == len(dataset["rewards"])
        return min(returns), max(returns)

    def __getitem__(self, index):
        """
        Overview:
            Get data by index
        Arguments:
            index (:obj:`int`): Index of data
        Returns:
            data (:obj:`dict`): Data dict
        
        .. note::
            The data dict contains the following keys:
            
            s (:obj:`torch.Tensor`): State
            a (:obj:`torch.Tensor`): Action
            r (:obj:`torch.Tensor`): Reward
            s_ (:obj:`torch.Tensor`): Next state
            d (:obj:`torch.Tensor`): Is finished
            fake_a (:obj:`torch.Tensor`): Fake action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
            fake_a_ (:obj:`torch.Tensor`): Fake next action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
        """

        data = self.storage.get(index=index)
        return data

    def __len__(self):
        return self.len

class GPCustomizedTensorDictDataset(GPTensorDictDataset):
    """
    Overview:
        Dataset for Generative Policy algorithm for customized data.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        env_id: str = None,
        action_augment_num: int = 16,
        numpy_data_path: str = None,
    ):
        """
        Overview:
            Initialization method of GPCustomizedDataset class
        Arguments:
            env_id (:obj:`str`): The environment id
            numpy_data_path (:obj:`str`): The path to the numpy data
        """

        super().__init__()

        data = np.load(numpy_data_path)

        self.states = torch.from_numpy(data["obs"]).float()
        self.actions = torch.from_numpy(data["action"]).float()
        self.next_states = torch.from_numpy(data["next_obs"]).float()
        reward = torch.from_numpy(data["reward"]).view(-1, 1).float()
        self.is_finished = torch.from_numpy(data["done"]).view(-1, 1).float()

        self.rewards = reward
        self.len = self.states.shape[0]
        log.info(f"{self.len} data loaded in GPCustomizedDataset")
        self.action_augment_num = action_augment_num
        self.storage = LazyTensorStorage(max_size=self.len)
        self.storage.set(
            range(self.len), TensorDict(
                {
                    "s": self.states,
                    "a": self.actions,
                    "r": self.rewards,
                    "s_": self.next_states,
                    "d": self.is_finished,
                    "fake_a": torch.zeros_like(self.actions).unsqueeze(1).repeat_interleave(action_augment_num, dim=1),
                    "fake_a_": torch.zeros_like(self.actions).unsqueeze(1).repeat_interleave(action_augment_num, dim=1),
                },
                batch_size=[self.len],
            )
        )

class GPDMcontrolTensorDictDataset(GPTensorDictDataset):
    def __init__(
        self,
        action_augment_num: int = None,
        tensor_data_path: str = None,
        file_paths: list = None,
    ):
        state_dicts = {}
        actions_list = []
        next_states_dicts = {}
        rewards_list = []
        for file_path in file_paths:
            data = torch.load(file_path)
            obs_keys = list(data[0]["s"].keys())
            
            for key in obs_keys:
                if key not in state_dicts:
                    state_dicts[key] = []
                    next_states_dicts[key] = []
                
                state_values = np.array([item["s"][key] for item in data], dtype=np.float32)
                next_state_values = np.array([item["s_"][key] for item in data], dtype=np.float32)
                
                state_dicts[key].append(torch.tensor(state_values))
                next_states_dicts[key].append(torch.tensor(next_state_values))
            
            actions_values = np.array([item["a"] for item in data], dtype=np.float32)
            rewards_values = np.array([item["r"] for item in data], dtype=np.float32).reshape(-1, 1)
            
            actions_list.append(torch.tensor(actions_values))
            rewards_list.append(torch.tensor(rewards_values))

        # Concatenate the tensors along the first dimension
        actions = torch.cat(actions_list, dim=0)
        rewards = torch.cat(rewards_list, dim=0)
        state_tensors = {key: torch.cat(state_dicts[key], dim=0) for key in obs_keys}
        next_state_tensors = {key: torch.cat(next_states_dicts[key], dim=0) for key in obs_keys}

        # Create TensorDicts
        state_tensordict = TensorDict(state_tensors, batch_size=[state_tensors[obs_keys[0]].shape[0]])
        next_state_tensordict = TensorDict(next_state_tensors, batch_size=[next_state_tensors[obs_keys[0]].shape[0]])

        # Combine everything into a final TensorDict
        final_tensordict = TensorDict({
            "actions": actions,
            "rewards": rewards,
            "states": state_tensordict,
            "next_states": next_state_tensordict
        }, batch_size=[actions.shape[0]])
        self.storage = LazyTensorStorage(max_size=self.len)
        self.storage.set(actions.shape[0],final_tensordict)



        
        