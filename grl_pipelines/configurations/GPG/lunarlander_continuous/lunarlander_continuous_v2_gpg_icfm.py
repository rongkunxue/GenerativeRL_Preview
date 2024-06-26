import torch
from easydict import EasyDict

action_size = 2
state_size = 8
device = torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
t_embedding_dim = 32
t_encoder = dict(
    type="GaussianFourierProjectionTimeEncoder",
    args=dict(
        embed_dim=t_embedding_dim,
        scale=30.0,
    ),
)
algorithm_type = "GPG"
solver_type = "ODESolver"
model_type = "IndependentConditionalFlowModel"
env_id = "LunarLanderContinuous-v2"
project_name = "LunarLanderContinuous-v2-GPG-ICFM"

model = dict(
    device=device,
    x_size=action_size,
    solver=dict(
        type="ODESolver",
        args=dict(
            library="torchdiffeq_adjoint",
        ),
    ),
    path=dict(
        sigma=0.1,
    ),
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
        wandb=dict(project=f"{env_id}-{algorithm_type}-{model_type}"),
        simulator=dict(
            type="GymEnvSimulator",
            args=dict(
                env_id="LunarLanderContinuous-v2",
            ),
        ),
        # dataset=dict(
        #     type="QGPOCustomizedDataset",
        #     args=dict(
        #         env_id="LunarLanderContinuous-v2",
        #         device=device,
        #         numpy_data_path = "./data.npz",
        #     ),
        # ),
        model=dict(
            GPPolicy=dict(
                device=device,
                model_type=model_type,
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
                epochs=500,
                # new add below
                lr_decy=False,
            ),
            sample_per_state=16,
            t_span=None if solver_type == "DPMSolver" else 32,
            critic=dict(
                batch_size=4096,
                epochs=500,
                learning_rate=3e-4,
                discount_factor=0.99,
                update_momentum=0.005,
                # new add below
                lr_decy=False,
            ),
            guided_policy=dict(
                batch_size=4096,
                epochs=200,
                learning_rate=1e-4,
                # new add below
                copy_from_basemodel=True,
                lr_decy=True,
                loss_type="double_minibatch_loss",
                grad_norm_clip=10,
                gradtime_step=32,
                lr_epochs=200,
                eta=1,
            ),
            evaluation=dict(
                eval=True,
                repeat=3,
                evaluation_iteration_interval=200,
                evaluation_behavior_policy_interval=50,
                evaluation_guided_policy_interval=10,
                guidance_scale=[0.0, 1.0, 2.0],
            ),
            checkpoint_path="./checkpoint",
            checkpoint_freq=100,
        ),
    ),
    deploy=dict(
        device=device,
        env=dict(
            env_id="LunarLanderContinuous-v2",
            seed=0,
        ),
        guidance_scale=2.0,
        num_deploy_steps=1000,
        t_span=None if solver_type == "DPMSolver" else 32,
    ),
)
