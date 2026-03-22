import json
import matplotlib.pyplot as plt
import numpy as np
import argparse
import os


plt.rcParams.update({
    'font.size': 16,
    'axes.labelsize': 16,
    'legend.fontsize': 14,
    'font.weight': 'bold',
    'axes.labelweight': 'bold',
    'axes.titleweight': 'bold',
    'figure.titleweight': 'bold'
})

plt.rcParams['font.family'] = 'serif'
plt.rcParams['font.serif'] = ['DejaVu Serif'] 

def load_data(filepath):
    """Loads the entire JSON content to access raw_data."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data

def get_stats(data_list, key):
    """Helper to calculate mean and standard deviation from raw data."""
    values = [item[key] for item in data_list]
    return np.mean(values), np.std(values, ddof=1) # ddof=1 for sample std dev

def create_dashboard(std_file, cvm_file, output_image="cvm_benchmark_dashboard.pdf"):
    # 1. Load Data
    std_data = load_data(std_file)
    cvm_data = load_data(cvm_file)
    
    # Shortcuts to sections
    std_sum = std_data['summary']
    cvm_sum = cvm_data['summary']
    std_raw = std_data['raw_data']
    cvm_raw = cvm_data['raw_data']

    labels = ['Standard VM', 'Confidential VM']
    x = np.arange(len(labels))
    width = 0.35

    # --- Setup Figure (1 Row, 3 Columns) ---
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(20, 7))
    plt.suptitle("Confidential Computing Impact Analysis: Agent Performance", fontsize=18, weight='bold')

    # ==========================================
    # PANEL 1: RUNTIME LATENCY (Stacked + Error Bars)
    # ==========================================
    # Get Component Averages for the Stack
    planning = [std_sum.get('avg_planning', 0), cvm_sum.get('avg_planning', 0)]
    tool_exec = [std_sum.get('avg_tool_exec', 0), cvm_sum.get('avg_tool_exec', 0)]
    processing = [std_sum.get('avg_processing', 0), cvm_sum.get('avg_processing', 0)]
    
    # Get TOTAL Statistics (Mean + Std Dev) from Raw Data
    std_total_mean, std_total_std = get_stats(std_raw, 'total_latency_ms')
    cvm_total_mean, cvm_total_std = get_stats(cvm_raw, 'total_latency_ms')
    
    total_means = [std_total_mean, cvm_total_mean]
    total_stds = [std_total_std, cvm_total_std]

    # Plotting Stacked Bars
    p1 = ax1.bar(x, planning, width, label='Planning (1st prompt)', color='#4e79a7')
    p2 = ax1.bar(x, tool_exec, width, bottom=planning, label='Tool Calling (Disk)', color='#f28e2b')
    bottom_proc = np.add(planning, tool_exec).tolist()
    p3 = ax1.bar(x, processing, width, bottom=bottom_proc, label='Processing (2nd prompt)', color='#e15759')

    # --- ADD ERROR BARS FOR TOTAL LATENCY ---
    # We overlay the error bar at the top of the stack
    ax1.errorbar(x, total_means, yerr=total_stds, fmt='none', ecolor='black', capsize=8, elinewidth=2, markeredgewidth=2)

    # Styling & Calculations
    overhead_latency = ((cvm_total_mean - std_total_mean) / std_total_mean) * 100
    max_latency = max(total_means) + max(total_stds)
    ax1.set_ylim(0, max_latency * 1.15)

    ax1.set_title(f"Total Latency per Request\nOverhead: +{overhead_latency:.1f}%", fontsize=18)
    ax1.set_ylabel('Milliseconds (ms)')
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.legend(fontsize=15)
    ax1.grid(axis='y', linestyle='--', alpha=0.3)

    # Labels
    for i, v in enumerate(total_means):
        ax1.text(i, v + total_stds[i] + (v*0.02), f"{v:.0f}ms", ha='center', va='bottom', fontweight='bold')


    # ==========================================
    # PANEL 2: THROUGHPUT PHYSICS (Grouped + Error Bars)
    # ==========================================
    # Get Stats
    std_pre_mean, std_pre_std = get_stats(std_raw, 'proc_prefill_tps')
    cvm_pre_mean, cvm_pre_std = get_stats(cvm_raw, 'proc_prefill_tps')
    
    std_dec_mean, std_dec_std = get_stats(std_raw, 'proc_decode_tps')
    cvm_dec_mean, cvm_dec_std = get_stats(cvm_raw, 'proc_decode_tps')

    prefill_means = [std_pre_mean, cvm_pre_mean]
    prefill_stds = [std_pre_std, cvm_pre_std]
    decode_means = [std_dec_mean, cvm_dec_mean]
    decode_stds = [std_dec_std, cvm_dec_std]

    # Plotting with YERR
    rects1 = ax2.bar(x - width/2, prefill_means, width, yerr=prefill_stds, capsize=5, label='Prefill (Context Read)', color='#76b7b2')
    rects2 = ax2.bar(x + width/2, decode_means, width, yerr=decode_stds, capsize=5, label='Decode (Generation)', color='#59a14f')

    # Styling
    drop_prefill = ((prefill_means[0] - prefill_means[1]) / prefill_means[0]) * 100
    drop_decode = ((decode_means[0] - decode_means[1]) / decode_means[0]) * 100

    max_tps = max(max(prefill_means), max(decode_means))
    ax2.set_ylim(0, max_tps * 1.2)

    ax2.set_title(f"LLM Token Throughput During Processing (2nd prompt)\nPrefill Overhead: +{drop_prefill:.1f}% | Decode Overhead: +{drop_decode:.1f}%", fontsize=18)
    ax2.set_ylabel('Tokens per Second (TPS)')
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels)
    ax2.legend(fontsize=15)
    ax2.grid(axis='y', linestyle='--', alpha=0.3)

    for rect in rects1 + rects2:
        height = rect.get_height()
        ax2.text(rect.get_x() + rect.get_width()/2., 1.01*height,
                f'{height:.2f}', ha='center', va='bottom', fontsize=15, fontweight='bold')


    # ==========================================
    # PANEL 3: STARTUP OVERHEAD (Grouped + Error Bars)
    # ==========================================
    # 1. Calculate Per-Run Totals for Std Dev
    def get_startup_totals(raw_list):
        return [r['mcp_connection_ms'] + r['tool_conversion_ms'] for r in raw_list]

    std_startup_vals = get_startup_totals(std_raw)
    cvm_startup_vals = get_startup_totals(cvm_raw)

    std_start_mean = np.mean(std_startup_vals)
    std_start_std = np.std(std_startup_vals, ddof=1)
    cvm_start_mean = np.mean(cvm_startup_vals)
    cvm_start_std = np.std(cvm_startup_vals, ddof=1)
    
    startup_means = [std_start_mean, cvm_start_mean]
    startup_stds = [std_start_std, cvm_start_std]

    # 2. Get Component Means for Stacked Bars
    conn_means = [std_sum.get('startup_mcp_connection', 0), cvm_sum.get('startup_mcp_connection', 0)]
    conv_means = [std_sum.get('startup_tool_conversion', 0), cvm_sum.get('startup_tool_conversion', 0)]

    # Plotting Stacked Bars
    p4 = ax3.bar(x, conn_means, width, label='MCP Handshake', color='#edc948')
    p5 = ax3.bar(x, conv_means, width, bottom=conn_means, label='Tool Parsing', color='#b07aa1')

    # --- ADD ERROR BARS FOR TOTAL STARTUP ---
    ax3.errorbar(x, startup_means, yerr=startup_stds, fmt='none', ecolor='black', capsize=8, elinewidth=2, markeredgewidth=2)

    # Styling
    overhead_startup = ((cvm_start_mean - std_start_mean) / std_start_mean) * 100
    
    max_startup = max(startup_means) + max(startup_stds)
    ax3.set_ylim(0, max_startup * 1.2)

    ax3.set_title(f"Cold Start Initialization\nOverhead: +{overhead_startup:.1f}%", fontsize=18)
    ax3.set_ylabel('Milliseconds (ms)')
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels)
    ax3.legend(loc='lower center', fontsize=15)
    ax3.grid(axis='y', linestyle='--', alpha=0.3)

    # Labels
    for i, v in enumerate(startup_means):
        ax3.text(i, v + startup_stds[i] + (v*0.01), f"{v:.2f}ms", ha='center', va='bottom', fontweight='bold')

    # Save
    plt.tight_layout(rect=[0, 0.03, 1, 0.95]) # Make room for suptitle
    plt.savefig(output_image, format='pdf', dpi=150)
    print(f"✅ Dashboard saved to {output_image}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--std", required=True, help="Path to Standard VM json report")
    parser.add_argument("--cvm", required=True, help="Path to Confidential VM json report")
    parser.add_argument("--out", default="pdfs/cvm_dashboard.pdf", help="Output filename")
    args = parser.parse_args()

    create_dashboard(args.std, args.cvm, args.out)