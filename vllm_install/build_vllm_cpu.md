## vLLM CPU Build Instructions

### 1. Environment Setup
Create a Python 3.10 virtual environment and install the necessary build tools.

```bash
cd vllm_install
uv venv --python 3.10
source .venv/bin/activate

# Install core build dependencies
uv pip install -U pip setuptools wheel packaging setuptools_scm cmake ninja
```
### 2. Install PyTorch (CPU Version)
Install the specific CPU wheels for PyTorch to avoid CUDA dependencies.
```bash
uv pip install \
  torch==2.10.0+cpu \
  torchvision==0.25.0+cpu \
  torchaudio==2.10.0+cpu \
  --index-url https://download.pytorch.org/whl/cpu \
  --no-cache
```

### 3. Clone and Modify pyproject.toml
Clone the repository and patch the build system requirements to accept the CPU version of Torch.
```bash
git clone https://github.com/vllm-project/vllm.git
cd vllm
git fetch --tags
git checkout 7e22309755aca3c

# Edit pyproject.toml:
# Locate the [build-system] section (requires).
# Update the torch line to: "torch == 2.10.0+cpu"
vim pyproject.toml
```
### 4. Install Runtime Dependencies
Install the required runtime packages from the requirements file.

```bash
uv pip install -r ../requirements-cpu.txt
```

### 5. Build vLLM
Here is our gcc, g++ version:
```bash
gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
Copyright (C) 2021 Free Software Foundation, Inc.
This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

g++ (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
Copyright (C) 2021 Free Software Foundation, Inc.
This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
```
Export the necessary environment variables to force a CPU build and install without build isolation.
```bash
export VLLM_TARGET_DEVICE=cpu
export VLLM_USE_CUDA=0
export CUDA_HOME=""
export FORCE_CUDA=0
export MAX_JOBS=$(nproc)
unset CFLAGS CXXFLAGS
export CMAKE_ARGS="-DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS_RELEASE=-O0"

# Install vLLM without dependencies to preserve the CPU Torch version, this step might takes long...
uv pip install --no-build-isolation --no-deps .
```
Expected: vLLM Version: 0.14.0rc2.dev278+g7e2230975
```bash
shichen@amd-sev-snp-cvm:~/dev/sandbox/vllm_install/vllm$ ls -lh build/lib.linux-x86_64-cpython-310/vllm/*.so
-rw-r--r-- 1 shichen shichen 4.3M Mar 24 11:55 build/lib.linux-x86_64-cpython-310/vllm/_C.abi3.so
```


### 6. Verification
After the build completes, verify that vLLM is actually running on the CPU:
```bash
python3 -c "import vllm; print(f'vLLM Version: {vllm.__version__}')"
```

## Host vLLM on CPU (note here I used port 8005)
```bash
vllm serve Qwen/Qwen2.5-0.5B-Instruct   --dtype float16   --max-model-len 8192   --max-num-seqs 1   --max-num-batched-tokens 8192   --swap-space 0   --enforce-eager   --port 8005  --enable-auto-tool-choice --tool-call-parser hermes --generation-config vllm
```