"""Report all four registered pure-C/grid cases, retaining adverse effects."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from run_multiscale_reference import read, save, sha, require
from audit_multiscale_capacity_axis import CASES


def report(run):
    protocol=read(run/"protocol.json");verified=read(run/"independent_verification.json")
    require(verified["passed"],"all-case audit must pass before interpretation")
    out=run/"analysis";require(not out.exists(),"preserve previous analysis");out.mkdir()
    rows=[];hourly=[];by_case={}
    for checked in verified["cases"]:
        name=checked["name"];metrics=checked["independent"]["metrics"]
        summary=read(run/"results"/name/"summary.json")
        interval=summary["solution_quality"]["solver_stages"][0]["objective_interval"]
        row={"case":name,"C_B_multiplier":1 if name.startswith("C1_") else 4,"shared_grid":name.endswith("shared"),**metrics,"solver_status":summary["solver_status"],"reported_mip_gap":summary["mip_gap"],"objective_lower_yuan":interval["lower"],"objective_upper_yuan":interval["upper"],"quality":summary["solution_quality"]["quality"]}
        for b in checked["thermal_ledger"]["buildings"]:
            for key,value in b.items():
                if key!="name":row[b["name"]+"_"+key]=value
        rows.append(row);by_case[name]=row
        tr=pd.read_csv(run/"results"/name/"building_trajectory.csv")
        frame=pd.read_csv(run/"results"/name/"input.csv")
        tr["case"]=name;tr["C_B_multiplier"]=row["C_B_multiplier"]
        tr["shared_grid"]=row["shared_grid"]
        power=tr.groupby("time_index",sort=True)[["heat_kw","cooling_kw"]].sum().sum(axis=1).to_numpy()
        index=tr.time_index.to_numpy(int)
        tr["system_import_kw"]=power[index]
        tr["reference_grid_capacity_kw"]=protocol["capacity_kw"]
        tr["reference_energy_charge_yuan"]=((tr.heat_kw+tr.cooling_kw).to_numpy()*frame.electricity_price.to_numpy()[index])
        tr["reference_average_factor_co2_kg"]=((tr.heat_kw+tr.cooling_kw).to_numpy()*frame.grid_emission_factor.to_numpy()[index])
        hourly.append(tr)
    pd.DataFrame(rows).to_csv(out/"case_metrics.csv",index=False)
    pd.concat(hourly,ignore_index=True).to_csv(out/"hourly.csv",index=False)
    effects={}
    for context in ("unlimited","shared"):
        low,high=by_case[f"C1_{context}"],by_case[f"C4_{context}"]
        effect={"comparison":f"C4_{context}-C1_{context}","only_design_C_changes":True}
        for key in ("reference_energy_charge_yuan","electricity_kwh","reference_average_factor_co2_kg","grid_peak_kw","variant_B_envelope_heat_loss_kwh","variant_B_mean_temperature_continuous_c"):
            effect[key+"_difference"]=high[key]-low[key]
        effect["bill_reduction_percent_denominator_C1"]=(low["reference_energy_charge_yuan"]-high["reference_energy_charge_yuan"])/low["reference_energy_charge_yuan"]*100
        effect["bill_difference_solver_interval"]={"lower":high["objective_lower_yuan"]-low["objective_upper_yuan"],"upper":high["objective_upper_yuan"]-low["objective_lower_yuan"]}
        effects["pure_C_"+context]=effect
    for factor in (1,4):
        free,shared=by_case[f"C{factor}_unlimited"],by_case[f"C{factor}_shared"]
        effects[f"grid_at_C{factor}"]={"only_shared_resource_changes":True,"bill_increase_yuan":shared["reference_energy_charge_yuan"]-free["reference_energy_charge_yuan"]}
        unconstrained=pd.read_csv(run/"results"/f"C{factor}_unlimited"/"building_trajectory.csv")
        import_power=unconstrained.groupby("time_index")[["heat_kw","cooling_kw"]].sum().sum(axis=1).to_numpy()
        effects[f"grid_at_C{factor}"]["unlimited_exceeds_reference_cap_hours"]=int(np.count_nonzero(import_power>protocol["capacity_kw"]+1e-6))
        effects[f"grid_at_C{factor}"]["unlimited_max_excess_reference_cap_kw"]=max(0.,float(import_power.max())-protocol["capacity_kw"])
        effects[f"grid_at_C{factor}"]["shared_binding_hours"]=shared["grid_binding_hours"]
    effects["bill_C_by_grid_interaction_yuan"]=effects["pure_C_shared"]["reference_energy_charge_yuan_difference"]-effects["pure_C_unlimited"]["reference_energy_charge_yuan_difference"]
    result={"rows":rows,"effects":effects,"negative_findings_retained":True,"interpretation":"Pure-C differences and resource interaction are conditional numerical responses of the declared1R1C ideal-equipment model. Increased model thermal inventory is not free measured storage or a general monotonic-cost theorem.","solver_bound_scope":"existing-assets dispatch objective only; audits verify feasibility and ledgers, not an independent optimality proof", "time_axis":protocol["time_axis"],"literature":protocol["literature"]}
    save(out/"summary.json",result)
    lines=["# 纯热容与公用电上限的完整时序对照","","四案均使用同一武汉真实网格气象与已登记公共原型参考。A保持H=.1043、C=.535576；B固定H=.15645，仅将C从.535576改为2.142304 kWh/℃。C倍率是研究设计轴，不是新增实测材料或BESTEST900建筑。所有案同样20–27℃、初末20℃、现有理想设备及2880小时天气；第二轴仅移除或保留6.4796375 kW公用电上限。","","| 案例 | 120日参考电费/元 | 用电/kWh | 固定平均因子CO₂/kg | 峰值/kW | 实际绑定小时 |","|---|---:|---:|---:|---:|---:|"]
    for r in rows: lines.append(f"| {r['case']} | {r['reference_energy_charge_yuan']:.6f} | {r['electricity_kwh']:.6f} | {r['reference_average_factor_co2_kg']:.6f} | {r['grid_peak_kw']:.6f} | {r['grid_binding_hours']} |")
    lines += ["",f"在共同电上限下，单独增大B热容的电费变化（C4−C1）为{effects['pure_C_shared']['reference_energy_charge_yuan_difference']:.6f}元，用电变化{effects['pure_C_shared']['electricity_kwh_difference']:.6f}kWh。无共同电上限下相应电费变化为{effects['pure_C_unlimited']['reference_energy_charge_yuan_difference']:.6f}元；两者差异即条件交互{effects['bill_C_by_grid_interaction_yuan']:.6f}元。这里保留能耗与碳的真实变化，不将省费、削峰混同节能。","","费用、逐时源/网约束与每个建筑的完整历史解析RC卷积独立核验；另保存完整变量向量，重建矩阵核对所有约束、边界和整数以及导出一一对应。独立热量账本解析积分每小时温度，核对净送热+增益=围护热损+库存变化，不使用终点温度矩形近似。审计本身不重新求解或另行证明最优性。","","文献到本轮实现的对应："]
    for source in protocol["literature"]: lines += [f"",f"- [{source['title']}]({source['url']})：{source['implementation']} 读取范围：{source['access']}"]
    lines += ["","这是既有1R1C方程内的设计轴因果对照，不能证明真实建筑的RC近似误差、聚合等效性、设备实测性能、三城普遍规律、联合投资最优或项目利润。计费仍是条件性120日能源费，碳为固定平均因子参考。保留两个独立建筑状态，未实现建筑聚合识别或精确时间聚合；Klatzer预印本的先验活跃集/对偶条件与尚待证明部分不会转化为本项目保证。"]
    (out/"RESULTS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(12,4),layout="constrained")
    labels=["C1 / free","C4 / free","C1 / shared","C4 / shared"]
    for ax,key,title in zip(axes,("reference_energy_charge_yuan","electricity_kwh","grid_peak_kw"),("120-day energy charge (CNY)","Electricity (kWh)","Actual import peak (kW)")):
        ax.bar(labels,[r[key] for r in rows],color=["#536b88","#329681","#536b88","#329681"])
        ax.set_title(title,fontsize=10);ax.tick_params(axis="x",rotation=25);ax.grid(axis="y",alpha=.2);ax.set_axisbelow(True)
        if key=="grid_peak_kw": ax.axhline(protocol["capacity_kw"],color="#bc4749",ls="--",label="Shared design cap");ax.legend(fontsize=8)
    fig.suptitle("Pure thermal-capacitance axis with identical H, service and weather",fontsize=12)
    fig.savefig(out/"capacity_grid_factorial.png",dpi=180);fig.savefig(out/"capacity_grid_factorial.svg");plt.close(fig)
    save(out/"manifest.json",{"run_manifest_sha256":sha(run/"manifest.json"),"audit_sha256":sha(run/"independent_verification.json"),"report_code_sha256":sha(Path(__file__)),"files":{f.name:sha(f) for f in sorted(out.iterdir()) if f.is_file()}})
    print({"analysis":str(out),"effects":effects},flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--run",type=Path,required=True)
    report(parser.parse_args().run.resolve())
