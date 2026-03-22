# MCP Agent Profiler

A demo code designed to measure the latency, throughput, and efficiency of LLM agents using the **Model Context Protocol (MCP)**. This tool specifically evaluates how quickly an agent can plan, interact with a local filesystem, and process large context returns in confidential VMs and standard VMs. In addition, we measure the system overhead introduced by Llama firewall (deberta and llm-based guardrails) for a better understanding of guardrail impacts on system utility. Our code is to be run on CPUs.

---

## 🚀 Overview

The profiler implements a `SimpleAgent` that interfaces with an MCP server to perform filesystem operations. It breaks down the agentic workflow into three measurable phases:

1.  **Planning Phase**: Time taken for the LLM to generate the initial tool call.
2.  **Tool Execution Phase**: The latency of the MCP server interacting with the OS to fetch data.
3.  **Processing Phase**: The LLM's performance while ingesting tool output (prefill) and generating a final response (decode).

---

## 📋 System Prerequisites
To enable AMD SEV SNP confidential VMs, follow the instruction here: https://github.com/SNPGuard/snp-guard
### 1. Our Hardware Environment (Host)
* **CPU:** AMD EPYC 7443P 24-Core Processor (Milan)
* **Cores:** 24 Cores / 48 Threads
* **Architecture:** `x86_64` (43 bits physical, 48 bits virtual)
* **Virtualization:** AMD-V with SEV, SEV-ES, and SEV-SNP support enabled.

### 2. Our Software Stack

| Component | Version / Build |
| :--- | :--- |
| **Host OS** | Ubuntu 22.04.5 LTS |
| **Host Kernel** | `6.16.0-snp-host-68799c0277b2` |
| **Guest OS** | Ubuntu 22.04.5 LTS |
| **Guest Kernel** | `6.16.0-snp-guest-038d61fd6422` |
| **QEMU** | `10.0.0` |

### 3.Running Confidential VM and standard VM:
To run Confidential VM
```bash
sudo <insert_path>/qemu-system-x86_64 -enable-kvm -cpu EPYC-v4,phys-bits=48 -machine q35 -smp 4,maxcpus=48 -m 10240M,slots=5,maxmem=20480M -no-reboot -bios <insert_path>/OVMF.fd -netdev user,id=vmnic,hostfwd=tcp::8765-:22 -device virtio-net-pci,disable-legacy=on,iommu_platform=true,netdev=vmnic,romfile= -drive file=<insert_path>/disk-ubuntu-22.04.qcow2,if=none,id=disk0,format=qcow2 -device virtio-scsi-pci,id=scsi0,disable-legacy=on,iommu_platform=true -device scsi-hd,drive=disk0 -machine confidential-guest-support=sev0,vmport=off -object memory-backend-memfd,id=ram1,size=10240M,share=true,prealloc=false -machine memory-backend=ram1 -object sev-snp-guest,id=sev0,policy=0x30000,cbitpos=51,reduced-phys-bits=1 -nographic -monitor pty -monitor unix:monitor,server,nowait
```
You can verify the enablement of sev-snp by issuing dmesg:
```bash
# Inside Confidential VM
(sandbox) ~/sandbox$ sudo dmesg | grep -i -e sev -i -e snp
[    0.000000] Linux version 6.16.0-snp-guest-038d61fd6422 ...(gcc (Ubuntu 11.4.0-1ubuntu1~22.04.2) 11.4.0, GNU ld (GNU Binutils for Ubuntu) 2.38) #2 SMP Thu Jan 22 14:27:24 CET 2026
[    0.000000] Command line: BOOT_IMAGE=/vmlinuz-6.16.0-snp-guest-038d61fd6422 root=/dev/mapper/ubuntu--vg-ubuntu--lv ro console=ttyS0
[    0.296060] Kernel command line: BOOT_IMAGE=/vmlinuz-6.16.0-snp-guest-038d61fd6422 root=/dev/mapper/ubuntu--vg-ubuntu--lv ro console=ttyS0
[    0.296116] Unknown kernel command line parameters "BOOT_IMAGE=/vmlinuz-6.16.0-snp-guest-038d61fd6422", will be passed to user space.
[    2.816629] Memory Encryption Features active: AMD SEV SEV-ES SEV-SNP
[    2.817767] SEV: Status: SEV SEV-ES SEV-SNP 
[    3.231942] SEV: APIC: wakeup_secondary_cpu() replaced with wakeup_cpu_via_vmgexit()
[    4.985266] SEV: Using SNP CPUID table, 28 entries present.
[    4.985550] SEV: SNP running at VMPL0.
[    5.316846] SEV: SNP guest platform devices initialized.
[    5.954784]     BOOT_IMAGE=/vmlinuz-6.16.0-snp-guest-038d61fd6422
[    7.315495] systemd[1]: Hostname set to <amd-sev-snp-cvm>.
[    7.885051] sev-guest sev-guest: Initialized SEV guest driver ...
```

To run standard VM
```bash
sudo <insert_path>/qemu-system-x86_64 -enable-kvm -cpu EPYC-v4,phys-bits=48 -machine q35 -machine vmport=off -machine memory-backend=ram1 -smp 4,maxcpus=48 -m 10240M,slots=5,maxmem=20480M -no-reboot -bios <insert_path>/OVMF.fd -netdev user,id=vmnic,hostfwd=tcp::8766-:22 -device virtio-net-pci,disable-legacy=on,netdev=vmnic,romfile= -drive file=<insert_path>/disk-ubuntu-standard-22.04.qcow2,if=none,id=disk0,format=qcow2 -device virtio-scsi-pci,id=scsi0,disable-legacy=on -device scsi-hd,drive=disk0 -object memory-backend-memfd,id=ram1,size=10240M,share=true,prealloc=false -nographic -monitor unix:monitor-std,server,nowait
```

--- 

## 📋 Software Prerequisites (install on both confidential VM and standard VM)
### 1. Install VLLM and Host it Locally
To host vLLM on CPU, check on `vllm_install\build_vllm_cpu.md`

### 2. Install cargo and mcp-server
1. to install rust&cargo:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```
2. restart the terminal or run this command:
```bash
source "$HOME/.cargo/env"
```
3. install the MCP filesystem server
```bash
cargo install mcp-server-filesystem --git https://github.com/modelcontextprotocol/servers
```
4. Verify the installation
```bash
which mcp-server-filesystem
```
5. update the `.env` file
```bash
echo "MCP_FILESYSTEM_BINARY=$(which mcp-server-filesystem)" >> .env
```

### 3. Installation of python packages
This project uses [uv](https://github.com/astral-sh/uv). To install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```
Then to install python dependencies:
```bash
# Install dependencies and create virtual environment for this agent environment
uv sync --locked
source .venv/bin/activate
```

### 4. Environment Configuration
Update the .env file in the root directory with your local paths and vLLM configuration:
```bash
# .env
VLLM_BASE_URL=http://localhost:8005/v1
VLLM_API_KEY=empty
TARGET_DIRECTORY=<path_to_this_project_directory>
MCP_FILESYSTEM_BINARY=<path_to_home_directory>/.cargo/bin/mcp-server-filesystem
HF_TOKEN="<your_huggingface_token>"
```

---

## 🚀 Usage - benchmark the agent, Figure 2 in paper
1. On **confidential VM**, run:
    ```bash
    MCP_USE_ANONYMIZED_TELEMETRY=false python benchmark.py --env cvm --iters 10
    ```
    - `--env`: String label for the test environment (affects the output filename).
    - `--iters`: Number of benchmark cycles to perform (default: 10).

    Expected Output: A `profile_cvm.json` will be generated in this projectory folder on the **confidential VM**.

2. On **standard VM**, run:
    ```bash
    MCP_USE_ANONYMIZED_TELEMETRY=false python benchmark.py --env std --iters 10
    ```
    Expected Output: A `profile_std.json` will be generated in this projectory folder on the **standard VM**.

3. Collect the `profile_cvm.json` (from **confidential VM**) and `profile_std.json` (from **standard VM**), place them under the `logs` folder, then run the visualization script:
    ```bash
    python visualize.py --std  logs/profile_std.json  --cvm logs/profile_cvm.json
    ```
    Expected Output: the plot for Figure 2 will be saved to `pdfs/cvm_dashboard.pdf`

---

## 🚀 Usage - Benchmarking Guardrail Overhead (Figures 3 & 4)

This section reproduces the guardrail overhead benchmarks on the **Confidential VM (CVM)** as presented in the paper.

### 📊 Figure 3: DeBERTa Trace Inspection
This experiment benchmarks the latency overhead when using DeBERTa to inspect the agent's execution trace.
1. **Generate the benchmark data** on **confidential VM**:
    ```bash
    MCP_USE_ANONYMIZED_TELEMETRY=false OPENAI_API_KEY=empty python benchmark_llamafirewall.py --env cvm --iters 10 --firewall --firewall-trace-type deberta
    ```
   Expected Output: a `logs/firewall_llama_True_profile_cvm_deberta.json` 

2. **Plot the results**
    ```bash
    # visualize guardrail overhead (deberta to filter tool output)
    python visualize_firewall_overhead.py --input_file logs/firewall_llama_True_profile_cvm_deberta.json --output_file pdfs/firewall_latency_drilldown_cvm_deberta.pdf --alignment_check deBERTa
    ```
    Expected Output: The final plot for Figure 3 will be saved to `pdfs/firewall_latency_drilldown_cvm_deberta.pdf`

### 📊 Figure 4: LLM Trace Inspection

This experiment benchmarks the latency overhead when using a LLM to inspect the agent's execution trace.
1. **Generate the benchmark data** on **confidential VM**:
    ```bash
    MCP_USE_ANONYMIZED_TELEMETRY=false OPENAI_API_KEY=empty python benchmark_llamafirewall.py --env cvm --iters 10 --firewall --firewall-trace-type llm
    ```
    Expected Output: `logs/firewall_llama_True_profile_cvm_llm.json` will be generated.

2. **Plot the results**
    ```bash
    # visualize guardrail overhead (use LLM to filter tool output)
    python visualize_firewall_overhead.py --input_file logs/firewall_llama_True_profile_cvm_llm.json --output_file pdfs/firewall_latency_drilldown_cvm_LLM.pdf --alignment_check LLM
    ```
    Expected Output: The final plot for Figure 4 will be saved to `pdfs/firewall_latency_drilldown_cvm_llm.pdf`.
<!-- --- -->

<!-- ## 📊 Understanding the Results
Upon completion, a file named profile_[env].json is generated. It contains a summary object with the following key metrics:
| Metric | Description |
| :--- | :--- |
| **avg_planning** | Latency (ms) for the model to decide on a tool (Planning TTFT). |
| **avg_tool_exec** | Time (ms) spent inside the MCP server (Filesystem I/O). |
| **avg_processing** | Latency (ms) for the final response after receiving tool results. |
| **avg_prefill_tps** | Throughput while reading the file list (Tokens Per Second). |
| **avg_decode_tps** | Throughput while generating the final answer. |
| **avg_ttft** | Average Time to First Token across the processing phase. | -->
