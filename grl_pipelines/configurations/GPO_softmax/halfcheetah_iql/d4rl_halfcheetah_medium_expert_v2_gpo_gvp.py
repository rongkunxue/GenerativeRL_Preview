import torch
from easydict import EasyDict

action_size = 6
state_size = 17
device = torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
t_embedding_dim = 32
t_encoder = dict(
    type="GaussianFourierProjectionTimeEncoder",
    args=dict(
        embed_dim=t_embedding_dim,
        scale=30.0,
    ),
)
algorithm_type = "GPO_softmax_sample"
solver_type = "ODESolver"
model_type = "DiffusionModel"
path = dict(type="gvp")
model_loss_type = "flow_matching"
env_id = "halfcheetah-medium-expert-v2"
project_name = f"d4rl-{env_id}-GPO-GVP"
model = dict(
    device=device,
    x_size=action_size,
    solver=(
        dict(
            type="DPMSolver",
            args=dict(
                order=2,
                device=device,
                steps=17,
            ),
        )
        if solver_type == "DPMSolver"
        else (
            dict(
                type="ODESolver",
                args=dict(
                    library="torchdiffeq",
                ),
            )
            if solver_type == "ODESolver"
            else dict(
                type="SDESolver",
                args=dict(
                    library="torchsde",
                ),
            )
        )
    ),
    path=path,
    reverse_path=path,
    model=dict(
        type="velocity_function",
        args=dict(
            t_encoder=t_encoder,
            backbone=dict(
                type="TemporalSpatialResidualNet",
                args=dict(
                    hidden_sizes=[512, 256, 128],
                    output_dim=action_size,
                    t_dim=t_embedding_dim,
                    condition_dim=state_size,
                    condition_hidden_dim=32,
                    t_condition_hidden_dim=128,
                ),
            ),
        ),
    ),
)

config = EasyDict(
    train=dict(
        project=project_name,
        device=device,
        wandb=dict(project=f"IQL-{env_id}-{algorithm_type}-{model_type}"),
        simulator=dict(
            type="GymEnvSimulator",
            args=dict(
                env_id=env_id,
            ),
        ),
        dataset=dict(
            type="GPOD4RLDataset",
            args=dict(
                env_id=env_id,
                device=device,
            ),
        ),
        model=dict(
            GPPolicy=dict(
                device=device,
                model_type=model_type,
                model_loss_type=model_loss_type,
                model=model,
                critic=dict(
                    device=device,
                    q_alpha=1.0,
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
                ),
            ),
            GuidedPolicy=dict(
                model_type=model_type,
                model=model,
            ),
        ),
        parameter=dict(
            algorithm_type=algorithm_type,
            behaviour_policy=dict(
                batch_size=4096,
                learning_rate=1e-4,
                epochs=2000,
            ),
            sample_per_state=16,
            t_span=None if solver_type == "DPMSolver" else 32,
            critic=dict(
                batch_size=4096,
                epochs=2000,
                learning_rate=3e-4,
                discount_factor=0.99,
                update_momentum=0.005,
                tau=0.7,
                method="iql",
            ),
            guided_policy=dict(
                batch_size=4096,
                epochs=10000,
                learning_rate=1e-4,
                eta=1.0,
            ),
            evaluation=dict(
                eval=True,
                repeat=10,
                evaluation_behavior_policy_interval=500,
                evaluation_guided_policy_interval=500,
                guidance_scale=[0.0, 1.0],
            ),
            checkpoint_path="./checkpoint",
            checkpoint_freq=100,
        ),
    ),
    deploy=dict(
        device=device,
        env=dict(
            env_id=env_id,
            seed=0,
        ),
        num_deploy_steps=1000,
        t_span=None if solver_type == "DPMSolver" else 32,
    ),
)
