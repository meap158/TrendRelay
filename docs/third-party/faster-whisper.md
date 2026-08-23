# SYSTRAN/faster-whisper

- Repository: https://github.com/SYSTRAN/faster-whisper
- Pinned revision: `ed9a06cd89a93e47838f564998a6c09b655d7f43`
- License: MIT (code and CTranslate2 conversions)
- Commercial use: allowed
- Status: embedded as the local speech-draft provider

Whisper reimplemented on CTranslate2, roughly four times faster than the
reference implementation at the same accuracy, with 8-bit quantisation
available on both CPU and GPU. A single stream fits comfortably in 6 GB.

TrendRelay installs it into the isolated media-AI runtime, downloads the chosen
model through a recoverable setup job, and keeps one loaded model per worker.
Device and compute type default to capability-tested `auto`: CUDA with FP16 (or
the next supported GPU type), otherwise CPU INT8. The CTranslate2 wheel carries
its own cuDNN but links cuBLAS without shipping it, so setting the provider up
on a machine with an NVIDIA GPU also fetches `nvidia-cublas-cu12` into the
runtime; without it CUDA fails at the first matrix multiply rather than when the
model is built. That download is skipped where there is no GPU, and a failed one
is reported as a skipped step rather than failing the setup - the CPU path is
the same transcription, only slower. The worker uses
`BatchedInferencePipeline` to batch a clip's speech chunks and holds the model
for the next item instead of loading hundreds of megabytes for every asset.
Explicit environment settings can pin the device, compute type, CPU threads,
and speech batch size when reproducibility or memory pressure matters.
