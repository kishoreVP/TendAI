# Local inference on a Jetson Orin Nano

**Status: integration implemented and tested with mocked HTTP only. Nothing in this repository
has been run on Jetson hardware yet.** The numbers to fill in below must come from a real device.

## Design

GammaPrime talks to any OpenAI-compatible `/v1/chat/completions` endpoint (`LocalProvider` in
`gammaprime/llm.py`). On the Jetson that endpoint is llama.cpp's `llama-server`, so the app itself
does not need CUDA, PyTorch or the Jetson software stack. It runs on a laptop/server, and the
Jetson is only the model host. If the Jetson is offline, rule-based extraction still works.
LLM tasks that are routed to it fail with a clear error and nothing silently switches to the cloud.

Structured output: requests send `response_format: {type: json_schema, ...}`. `llama-server`
converts the JSON schema to a grammar. The app still validates every response with `jsonschema` and
retries once before reporting an error.

## Model choice (to be confirmed by measurement)

| Candidate | Size (Q4_K_M GGUF) | Why |
|---|---|---|
| Qwen2.5-1.5B-Instruct | ~1.0 GB | fits easily in 8 GB shared memory, fastest |
| Qwen2.5-3B-Instruct | ~1.9 GB | better extraction quality; still leaves room for a 8k context KV cache |

Start with 3B Q4_K_M and a context of 4096 tokens; clause-level prompts in GammaPrime are short
(< 1k tokens). Check the model card licence before redistribution (Qwen2.5-3B uses the Qwen
Research licence; 1.5B is Apache-2.0).

## Setup steps (on the Jetson)

1. Check the platform: `cat /etc/nv_tegra_release`, `nvcc --version`, `uname -m` (expect aarch64).
   Note the JetPack / L4T and CUDA versions; do not install desktop CUDA or x86 wheels.
2. Build llama.cpp with CUDA for the Orin GPU (compute capability 8.7):
   ```bash
   git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
   cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87
   cmake --build build -j4 --target llama-server
   ```
3. Download a GGUF (e.g. `Qwen2.5-3B-Instruct-Q4_K_M.gguf`) and serve it:
   ```bash
   ./build/bin/llama-server -m Qwen2.5-3B-Instruct-Q4_K_M.gguf -ngl 99 -c 4096 --host 0.0.0.0 --port 8080
   ```
4. On the GammaPrime machine: `GP_DEFAULT_PROVIDER=local GP_LOCAL_LLM_URL=http://<jetson>:8080/v1`.

## Test profile (fill in from real runs)

Run `python scripts/bench_llm.py` from the GammaPrime machine while `sudo tegrastats` runs on the Jetson.

| Metric | Value | How measured |
|---|---|---|
| JetPack / L4T / CUDA | _not measured_ | `/etc/nv_tegra_release` |
| Model load time | _not measured_ | llama-server log |
| Peak memory | _not measured_ | tegrastats RAM |
| First-call latency | _not measured_ | bench_llm.py |
| Mean latency per clause | _not measured_ | bench_llm.py |
| Valid structured output | _not measured_ | bench_llm.py |
| Expected value recovered | _not measured_ | bench_llm.py |
| Behaviour when server stops mid-run | _not measured_ | stop server during bench |

## Suggested routing

Deterministic rules handle everything the sample tenders needed. Use the LLM only for clauses the
rules don't understand (`llm_candidates` in `requirements.py`) and for unparsed bidder passages:

```env
GP_TASK_ROUTING={"requirement_extraction": "groq", "evidence_mapping": "local"}
GP_ALLOW_CLOUD_FOR_BIDDER_DOCS=false
```

Here tender text (public) may go to Groq, and confidential bidder text stays on the Jetson.
