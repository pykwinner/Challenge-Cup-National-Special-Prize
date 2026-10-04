        # 以下为build方法内部片段，依赖既有模型、时序与变量登记表。
        # Storage investments and operation. Short-term storage can be cyclic;
        # seasonal storage can use fixed initial/final boundary conditions.
        for st_name, storage in self.storages.items():
            # existing和max_new均为能量容量kWh，不是充放功率kW。
            existing = float(storage.get("existing_capacity_kwh", 0.0))
            max_new = float(storage.get("max_new_capacity_kwh", 0.0))
            cap_idx = None
            # 只有允许新增容量时才建立投资变量；既有容量作为常量。
            if max_new > 0:
                cap_idx = p.add_var(f"storage_capacity[{st_name}]", ub=max_new)
                self.variables["storage_capacity"][st_name] = cap_idx
                self._record_investment_cost(
                    cap_idx, "storage_capacity", st_name, float(storage.get("capex_yuan_per_kwh", 0.0))
                )

            # 功率能量比乘装机能量容量得到逐时充放功率上限。
            power_ratio = float(storage.get("power_to_energy_ratio_per_hour", 0.25))
            max_power = power_ratio * (existing + max_new)
            # 严格模式按配置启用；关闭时不能宣称二元互斥已实施。
            strict_storage_mode = bool(storage.get("strict_charge_discharge_mode", True))
            throughput_cost = float(
                storage.get("throughput_cost_yuan_per_kwh", 0.0)
            )
            # 每一真实时段建立充热、放热和SOC；状态连续性在后文连接。
            for t in self.times:
                ch = p.add_var(f"storage_charge[{st_name},{t}]")
                dis = p.add_var(f"storage_discharge[{st_name},{t}]")
                soc = p.add_var(f"storage_soc[{st_name},{t}]")
                self.variables["storage_charge"][(st_name, t)] = ch
                self.variables["storage_discharge"][(st_name, t)] = dis
                self.variables["storage_soc"][(st_name, t)] = soc
                # 可选吞吐费用作用于充放热量，不改变能量守恒方程。
                if throughput_cost > 0.0:
                    self._record_time_component(
                        "operating_cost", ch, throughput_cost, t
                    )
                    self._record_time_component(
                        "operating_cost", dis, throughput_cost, t
                    )

                # 新增容量以负系数进入上界，形成SOC与装机容量的线性关系。
                # 该行左端为SOC减新增容量，右端保留既有容量。
                soc_row = {soc: 1.0}
                ch_row = {ch: 1.0}
                dis_row = {dis: 1.0}
                if cap_idx is not None:
                    soc_row[cap_idx] = -1.0
                    ch_row[cap_idx] = -power_ratio
                    dis_row[cap_idx] = -power_ratio
                p.add_constraint(soc_row, ub=existing)
                p.add_constraint(ch_row, ub=power_ratio * existing)
                p.add_constraint(dis_row, ub=power_ratio * existing)
                # M取候选最大功率常数；不使用投资变量乘二元变量。
                if strict_storage_mode:
                    mode = p.add_var(f"storage_mode[{st_name},{t}]", ub=1.0, integer=True)
                    self.variables["storage_mode"][(st_name, t)] = mode
                    p.add_constraint({ch: 1.0, mode: -max_power}, ub=0.0)
                    p.add_constraint({dis: 1.0, mode: max_power}, ub=max_power)

            # cyclic与fixed是两种边界身份，不能一律要求初末SOC相等。
            boundary_mode = str(storage.get("boundary_mode", "cyclic"))
            # 自损失按小时给定，再按实际步长取幂换算。
            loss_factor = (1.0 - float(storage.get("self_loss_per_hour", 0.0))) ** self.dt
            eta_ch = float(storage.get("charge_efficiency", 0.95))
            eta_dis = float(storage.get("discharge_efficiency", 0.95))
            if boundary_mode not in {"cyclic", "fixed"}:
                raise ValueError(f"储热 {st_name} 的 boundary_mode 仅支持 cyclic 或 fixed")
            # 充放功率乘步长后才是能量；放热需除以放热效率。
            for t in self.times:
                row = {
                    self.variables["storage_soc"][(st_name, t)]: 1.0,
                    self.variables["storage_charge"][(st_name, t)]: -eta_ch * self.dt,
                    self.variables["storage_discharge"][(st_name, t)]: self.dt / eta_dis,
                }
                # 状态仅在所属边界组内衔接，禁止跨无关场景串联。
                group_times = self.boundary_groups[self.time_boundary_group[t]]
                is_group_start = t == group_times[0]
                # 循环模式使用组内前驱；非首时段同样递推。
                if boundary_mode == "cyclic" or not is_group_start:
                    prev = self.time_predecessor[t]
                    self._add(row, self.variables["storage_soc"][(st_name, prev)], -loss_factor)
                    p.add_constraint(row, lb=0.0, ub=0.0)
                else:
                    # 固定边界以装机能量容量的声明比例给定初态。
                    initial_fraction = float(storage.get("initial_soc_fraction", 0.5))
                    if cap_idx is not None:
                        row[cap_idx] = -loss_factor * initial_fraction
                    # 首小时先计初始储热的自损，再计本小时充放。
                    initial_energy = loss_factor * initial_fraction * existing
                    p.add_constraint(row, lb=initial_energy, ub=initial_energy)
            # 固定模式期末是最低SOC比例约束，不强制等于初态。
            if boundary_mode == "fixed":
                final_fraction = float(storage.get("final_soc_min_fraction", 0.0))
                for group_times in self.boundary_groups.values():
                    final_row = {self.variables["storage_soc"][(st_name, group_times[-1])]: 1.0}
                    if cap_idx is not None:
                        final_row[cap_idx] = -final_fraction
                    # 期末库存下界与总装机容量保持同一比例。
                    p.add_constraint(final_row, lb=final_fraction * existing)
