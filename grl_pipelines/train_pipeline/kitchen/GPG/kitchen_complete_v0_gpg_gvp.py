import gym
import d4rl

from grl.algorithms.gp import GPAlgorithm
from grl.utils.log import log
from grl_pipelines.configurations.GPG.kitchen.kitchen_complete_v0_gpg_gvp import (
    config,
)


def gpo_pipeline(config):

    gpo = GPAlgorithm(config)

    # ---------------------------------------
    # Customized train code ↓
    # ---------------------------------------
    gpo.train()
    # ---------------------------------------
    # Customized train code ↑
    # ---------------------------------------

    # ---------------------------------------
    # Customized deploy code ↓
    # ---------------------------------------
    agent = gpo.deploy()
    env = gym.make(config.deploy.env.env_id)
    observation = env.reset()
    for _ in range(config.deploy.num_deploy_steps):
        env.render()
        observation, reward, done, _ = env.step(agent.act(observation))
    # ---------------------------------------
    # Customized deploy code ↑
    # ---------------------------------------


if __name__ == "__main__":
    log.info("config: \n{}".format(config))
    gpo_pipeline(config)
