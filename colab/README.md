# Colab compute backend

`exactory-lab run --backend colab` runs one experiment script on a Colab GPU.
Colab is compute only: it never does ideation, writing, or review, and no
LLM runs there. The transport is one shared folder, with no Google API and
no OAuth code:

```
exactory-lab run --backend colab        colab_runner.ipynb (GPU runtime)
   |  write job + READY  ------------>  <sync>/jobs/<job_id>/
   |                                        | run the script on the GPU
   |  poll for DONE  <---------------   <sync>/results/<job_id>/
   v
experiment/            <- logs, results, and plots pulled back, same layout
```

## Setup, once

1. Install Google Drive for Desktop, so a Drive folder mirrors to your disk.
2. Open `colab_runner.ipynb` in Colab, pick a GPU runtime, run the cell, and
   approve the Drive mount. It creates and serves `My Drive/exactory-colab`.
3. Export the local mirror path before you start Claude Code:

   ```
   export EXACTORY_LAB_COLAB_DIR="$HOME/Google Drive/My Drive/exactory-colab"
   ```

`exactory-lab colab-status` tells you whether the runner's heartbeat is
fresh. Optional tuning: `EXACTORY_LAB_COLAB_SYNC_WAIT` (seconds before the
READY marker, default 10, beats the Drive sync race),
`EXACTORY_LAB_COLAB_POLL` (poll interval, default 10), and
`EXACTORY_LAB_COLAB_WAIT` (extra deadline beyond the job timeout, default
1800).

`exactory-lab colab-serve` runs the same job loop locally on the CPU: a
stand-in for dry-running the path without a Colab session.
