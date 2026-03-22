import json
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import ConnectionPatch
import argparse
import os
import sys


def get_stats(data_list, key):
    """Helper to calculate mean and standard deviation from raw data."""
    values = [item[key] for item in data_list]
    return np.mean(values), np.std(values, ddof=1) # ddof=1 for sample std dev


def main(INPUT_FILE, OUTPUT_FILE, ALIGNMENT_CHECK):
    # 1. READ FILE
    if not os.path.exists(INPUT_FILE):
        print(f"⚠️ File '{INPUT_FILE}' not found. Using dummy data.")
        # Dummy data for testing logic (GPT generated logic)
        data = {
             "summary": {
                "avg_total": 115481.59,
                "avg_firewall_overhead_input": 2418.26,
                "avg_firewall_overhead_output": 19.52,
                "avg_fw_times_overhead_tool": 2469.36,
                "avg_fw_times_overhead_alignment": 2346.59
            },
            # added dummy raw data for std dev calc
            "raw_data": [
                {"firewall_overhead_ms_input": 2400, "firewall_overhead_ms_output": 20, "firewall_overhead_ms_tool": 2500, "firewall_overhead_ms_alignment": 2300},
                {"firewall_overhead_ms_input": 2436, "firewall_overhead_ms_output": 19, "firewall_overhead_ms_tool": 2438, "firewall_overhead_ms_alignment": 2392}
            ]
        }
    else:
        with open(INPUT_FILE, 'r') as f:
            data = json.load(f)

    s = data.get("summary", data)
    raw = data.get('raw_data', [])

    # 2. EXTRACT METRICS (ms)
    total_latency = s.get("avg_total", 0)
    fw_input = s.get("avg_firewall_overhead_input", 0)
    fw_output = s.get("avg_firewall_overhead_output", 0)
    fw_tool = s.get("avg_fw_times_overhead_tool", 0)
    fw_alignment = s.get("avg_fw_times_overhead_alignment", 0)
    
    total_security = fw_input + fw_output + fw_tool + fw_alignment
    core_agent_time = total_latency - total_security

    # calculate the overhead standard deviation across guardrails
    # Check if raw data exists to avoid errors on dummy data
    if raw:
        input_mean, input_stdd = get_stats(raw, "firewall_overhead_ms_input")
        output_mean, output_stdd = get_stats(raw, "firewall_overhead_ms_output")
        tool_mean, tool_stdd = get_stats(raw, "firewall_overhead_ms_tool")
        alignment_mean, alignment_stdd = get_stats(raw, "firewall_overhead_ms_alignment")
    else:
        input_stdd = output_stdd = tool_stdd = alignment_stdd = 0

    # 3. SETUP PLOT
    plt.rcParams['font.family'] = 'serif'
    plt.rcParams['font.serif'] = ['DejaVu Serif', 'Times New Roman', 'serif']
    plt.rcParams['text.color'] = '#2c3e50'
    plt.rcParams['font.size'] = 14
    plt.rcParams['font.weight'] = 'bold'      
    plt.rcParams['axes.labelweight'] = 'bold' 
    plt.rcParams['axes.titleweight'] = 'bold' 
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 9)) 
    
    plt.subplots_adjust(wspace=0.6, top=0.85, bottom=0.1, left=0.05, right=0.95)
    
    # --- LEFT PANEL: MACRO VIEW (Donut) ---
    macro_labels = ['Core Agent Logic\n(LLM Inference)', 'Security Overhead\n(LlamaFirewall)']
    macro_values = [core_agent_time, total_security]
    macro_colors = ['#bdc3c7', '#e74c3c'] 
    
    wedges, texts, autotexts = ax1.pie(
        macro_values, 
        labels=macro_labels,
        autopct='%1.1f%%',
        startangle=45,
        colors=macro_colors,
        explode=(0, 0.05),
        wedgeprops=dict(width=0.6, edgecolor='white'),
        textprops={'fontsize': 16, 'weight': 'bold'},
        pctdistance=0.85 
    )
    
    ax1.set_title("Total Request Time Distribution", fontsize=20, pad=20, weight='bold')
    
    plt.setp(autotexts, size=16, color="black", weight='bold')
    plt.setp(autotexts[1], color="white", weight='bold') 
    
    # --- RIGHT PANEL: MICRO VIEW (Bar Chart) ---
    micro_labels = ['Input Guardrail', 'Tool Guardrail', f'Action Guardrail ({ALIGNMENT_CHECK})', 'Output Guardrail']
    micro_values = [fw_input, fw_tool, fw_alignment, fw_output]
    micro_errors = [input_stdd, tool_stdd, alignment_stdd, output_stdd]
    micro_colors = ['#e67e22', '#c0392b', '#d35400', '#f1c40f']
    
    y_pos = np.arange(len(micro_labels))
    
    # --- UPDATED: Added xerr (Error Bars) and capsize ---
    bars = ax2.barh(y_pos, micro_values, xerr=micro_errors, capsize=8, color=micro_colors)
    
    ax2.set_facecolor('#f9f9f9') 
    ax2.set_frame_on(True)
    ax2.spines['right'].set_visible(False)
    
    for spine in ['top', 'bottom', 'left']:
        ax2.spines[spine].set_visible(True)
        ax2.spines[spine].set_color('#333333')
        ax2.spines[spine].set_linewidth(2.0)
        
    ax2.tick_params(right=False, labelright=False)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(micro_labels, fontsize=16, weight='bold') 
    ax2.invert_yaxis() 
    ax2.set_xlabel('Latency (Milliseconds)', fontsize=16, weight='bold')
    ax2.set_title("Zoom-in: Security Overhead Components", fontsize=20, pad=20, weight='bold')
    
    # --- UPDATED: Dynamic Label Positioning ---
    for i, bar in enumerate(bars):
        width = bar.get_width()
        error = micro_errors[i]
        
        # Calculate position: End of bar + Error bar length + Padding
        # We add a small buffer (2% of max value) so text doesn't touch the error bar
        x_pos = width + error + (max(micro_values) * 0.02)
        
        # If value is super small (like output guardrail), ensure a minimum offset
        if x_pos < 100: x_pos = 100

        ax2.text(x_pos, bar.get_y() + bar.get_height()/2, 
                 f'{width:.0f} ms\n(±{error:.0f})', # Added std dev to text
                 ha='left', va='center', fontsize=14, color='#333', weight='bold')

    # --- CONNECTION LINES ---
    theta1, theta2 = wedges[1].theta1, wedges[1].theta2
    center, r = wedges[1].center, wedges[1].r
    
    con_style = dict(color="black", lw=2.0)

    # Top Line
    x = r * np.cos(np.radians(theta2))
    y = r * np.sin(np.radians(theta2))
    con1 = ConnectionPatch(xyA=(x, y), coordsA=ax1.transData,
                           xyB=(0, 1), coordsB=ax2.transAxes, 
                           **con_style)
    
    # Bottom Line
    x = r * np.cos(np.radians(theta1))
    y = r * np.sin(np.radians(theta1))
    con2 = ConnectionPatch(xyA=(x, y), coordsA=ax1.transData,
                           xyB=(0, 0), coordsB=ax2.transAxes, 
                           **con_style)
    
    fig.add_artist(con1)
    fig.add_artist(con2)

    # Main Title
    security_pct = (total_security / total_latency) * 100
    plt.suptitle(f"Guardrail System Impact Analysis ({ALIGNMENT_CHECK} for Action Guardrail)\nTotal Security Tax: {total_security/1000:.2f}s ({security_pct:.1f}%)", 
                 fontsize=24, color='#2c3e50', weight='bold')
    
    plt.savefig(OUTPUT_FILE, dpi=300, bbox_inches='tight')
    print(f"✅ Final PDF chart saved to {OUTPUT_FILE}")
    plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_file", default="logs/firewall_llama_True_profile_cvm_alignment_check.json", help="Path to input json log file")
    parser.add_argument("--output_file", default="pdfs/firewall_latency_drilldown_cvm_alignment_check.pdf", help="Path to output pdf file")
    parser.add_argument("--alignment_check", default="deBERTa", choices=["deBERTa", "LLM"], help="Path to output pdf file")
    args = parser.parse_args()
    main(args.input_file, args.output_file, args.alignment_check)