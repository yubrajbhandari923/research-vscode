# RESEARCH.md: scientific context for agents

## Question
How does the diffusion coefficient D control the spreading of a heat pulse? Expect the variance of the pulse (sum of both axes) to grow linearly in time with slope 4*D.

## Numerical conventions (do not guess; follow exactly)
- Equation u_t = D (u_xx + u_yy) on an n x n grid, dx = 1, periodic boundaries, explicit FTCS.
- Initial condition: Gaussian pulse (sigma0 = 4 cells) centred in the grid, total mass 1.
- Time step dt = 0.2 / (4 D)  (so D*dt/dx^2 = 0.05, stable). Default n = 64, steps = 400.
- Variance = sum over both axes of the second central moment of u (so it grows as 4*D*t).

## Environment
- Local and cluster Python have NumPy; the cluster Python is 3.8 with NumPy >= 1.17, so use only features available there.
- matplotlib is available locally only (plotting happens locally).
- Slurm partition defq. One CPU, 1G memory, 5 minutes per job is plenty; never ask for more.

## Definition of done
- The solver reproduces d(variance)/dt = 4D within 1% for D = 0.05, 0.10, 0.20.
- Three Slurm runs (one per D) completed; their metrics and output files are registered and came home.
- One publication-quality comparison figure (variance vs time per D with the 4Dt line, growth rate vs D).
