import torch
from easydict import EasyDict

path = ""
domain_name = "cheetah"
task_name = "run"
env_id = f"{domain_name}-{task_name}"
algorithm = "SRPO"
action_size = 6
state_size = 17

project_name = f"{env_id}-{algorithm}"
device = torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
t_embedding_dim = 32
t_encoder = dict(
    type="GaussianFourierProjectionTimeEncoder",
    args=dict(
        embed_dim=t_embedding_dim,
        scale=30.0,
    ),
)
solver_type = "DPMSolver"
action_augment_num = 16

config = EasyDict(
    train=dict(
        project=project_name,
        simulator=dict(
            type="DeepMindControlEnvSimulator",
            args=dict(
                domain_name=domain_name,
                task_name=task_name,
                dict_return=False,
            ),
        ),
        dataset=dict(
            type="QGPODeepMindControlTensorDictDataset",
            args=dict(
                path=path,
                action_augment_num=action_augment_num,
            ),
        ),
        model=dict(
            SRPOPolicy=dict(
                device=device,
                policy_model=dict(
                    state_dim=state_size,
                    action_dim=action_size,
                    layer=2,
                ),
                critic=dict(
                    device=device,
                    adim=action_size,
                    sdim=state_size,
                    layers=2,
                    update_momentum=0.95,
                    DoubleQNetwork=dict(
                        backbone=dict(
                            type="ConcatenateMLP",
                            args=dict(
                                hidden_sizes=[action_size + state_size, 256, 256],
                                output_size=1,
                                activation="relu",
                            ),
                        ),
                    ),
                    VNetwork=dict(
                        backbone=dict(
                            type="MultiLayerPerceptron",
                            args=dict(
                                hidden_sizes=[state_size, 256, 256],
                                output_size=1,
                                activation="relu",
                            ),
                        ),
                    ),
                ),
                diffusion_model=dict(
                    device=device,
                    x_size=action_size,
                    alpha=1.0,
                    beta=0.1,
                    solver=dict(
                        type="DPMSolver",
                        args=dict(
                            order=2,
                            device=device,
                            steps=17,
                        ),
                    ),
                    path=dict(
                        type="linear_vp_sde",
                        beta_0=0.1,
                        beta_1=20.0,
                    ),
                    model=dict(
                        type="noise_function",
                        args=dict(
                            t_encoder=t_encoder,
                            backbone=dict(
                                type="TemporalConcatenateMLPResNet",
                                args=dict(
                                    input_dim=state_size + action_size,
                                    output_dim=action_size,
                                    num_blocks=3,
                                ),
                            ),
                        ),
                    ),
                ),
            )
        ),
        parameter=dict(
            behaviour_policy=dict(
                batch_size=4096,
                learning_rate=3e-4,
                iterations=2000,
            ),
            critic=dict(
                batch_size=4096,
                iterations=2000,
                learning_rate=3e-4,
                discount_factor=0.99,
                tau=0.7,
                update_momentum=0.005,
                checkpoint_freq=10,
            ),
            policy=dict(
                batch_size=256,
                learning_rate=3e-4,
                tmax=2000000,
                iterations=200000,
            ),
            evaluation=dict(
                evaluation_interval=100,
                repeat=10,
            ),
            checkpoint_path=f"./{env_id}-{algorithm}",
        ),
    ),
    deploy=dict(
        device=device,
        env=dict(
            env_id=env_id,
            seed=0,
        ),
        num_deploy_steps=1000,
    ),
)

if __name__ == "__main__":

    import gym

    from grl.algorithms.srpo import SRPOAlgorithm
    from grl.utils.log import log

    def srpo_pipeline(config):

        srpo = SRPOAlgorithm(config)

        # ---------------------------------------
        # Customized train code ↓
        # ---------------------------------------
        srpo.train()
        # ---------------------------------------
        # Customized train code ↑
        # ---------------------------------------

        # ---------------------------------------
        # Customized deploy code ↓
        # ---------------------------------------
        agent = srpo.deploy()
        env = gym.make(config.deploy.env.env_id)
        env.reset()
        for _ in range(config.deploy.num_deploy_steps):
            env.render()
            env.step(agent.act(env.observation))
        # ---------------------------------------
        # Customized deploy code ↑
        # ---------------------------------------

    log.info("config: \n{}".format(config))
    srpo_pipeline(config)
