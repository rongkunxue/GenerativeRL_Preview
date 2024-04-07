import torch
from easydict import EasyDict

action_size = 6
state_size = 17
device = torch.device('cuda:0') if torch.cuda.is_available() else torch.device('cpu')

t_embedding_dim = 64  #CHANGE

t_encoder = dict(
    type = "GaussianFourierProjectionTimeEncoder",
    args = dict(
        embed_dim = t_embedding_dim,
        scale = 30.0,
    ),
)

config = EasyDict(
    train = dict(
        project = 'd4rl-halfcheetah-v2-qgpo',
        simulator = dict(
            type = "GymEnvSimulator",
            args = dict(
                env_id = 'HalfCheetah-v2',
            ),
        ),
        dataset = dict(
            type = "D4RLDataset",
            args = dict(
                env_id = "halfcheetah-medium-expert-v2",
                device = device,
            ),
        ),
        model = dict(
            SRPOPolicy = dict(
                device = device,
                policy_model = dict(
                    state_dim = state_size,
                    action_dim = action_size,
                    layer = 2,
                ),

                critic = dict(
                    device = device,
                    adim = action_size,
                    sdim = state_size ,
                    layers = 2,
                    update_momentum = 0.95,
                ),

                diffusion_model = dict(
                    device = device,
                    x_size = action_size,
                    alpha = 1.0,
                    solver = dict(
                        # type = "ODESolver",
                        # args = dict(
                        #     library="torchdyn",
                        # ),
                        type = "DPMSolver",
                        args = dict(
                            order=2,
                            device=device,
                            steps=17,
                        ),
                    ),
                    path = dict(
                        type = "linear_vp_sde",
                        beta_0 = 0.1,
                        beta_1 = 20.0,
                    ),
                    model = dict(
                        type = "noise_function",
                        args = dict(
                            t_encoder = t_encoder,
                            backbone = dict(
                                type = "ALLCONCATMLP",
                                args = dict(
                                    input_dim= state_size+action_size,
                                    output_dim=action_size,
                                    num_blocks=3,
                                ),
                            ),
                        ),
                    ),
                ),
            )
        ),
        parameter = dict(
            behaviour_policy = dict(
                batch_size = 2048,
                learning_rate = 3e-4,
                iterations = 10000,
            ),
            sample_per_state = 16,

            critic = dict(
                batch_size = 256,
                stop_training_iterations = 1500000,
                learning_rate = 3e-4,
                discount_factor = 0.99,
                update_momentum = 0.995,
            ),
            energy_guidance = dict(
                iterations = 600000,
                learning_rate = 3e-4,
            ),
            evaluation = dict(
                evaluation_interval = 10000,
                guidance_scale = [0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0],
            ),
        ),
    ),
    deploy = dict(
        device = device,
        env = dict(
            env_id = 'HalfCheetah-v2',
            seed = 0,
        ),
        num_deploy_steps = 1000,
    ),
)

