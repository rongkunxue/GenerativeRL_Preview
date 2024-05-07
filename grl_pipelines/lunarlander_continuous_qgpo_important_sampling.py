import gym

from grl.algorithms.qgpo_important_sampling import QGPOISAlgorithm
from grl.datasets import QGPOCustomizedDataset
from grl.utils.log import log
from grl_pipelines.configurations.lunarlander_continuous_qgpo_important_sampling import (
    config,
)


def qgpo_pipeline(config):

    qgpo = QGPOISAlgorithm(
        config,
        dataset=QGPOCustomizedDataset(
            numpy_data_path="./data.npz", device=config.train.device
        ),
    )

    # ---------------------------------------
    # Customized train code ↓
    # ---------------------------------------
    qgpo.train()
    # ---------------------------------------
    # Customized train code ↑
    # ---------------------------------------

    # ---------------------------------------
    # Customized deploy code ↓
    # ---------------------------------------
    agent = qgpo.deploy()
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
    qgpo_pipeline(config)