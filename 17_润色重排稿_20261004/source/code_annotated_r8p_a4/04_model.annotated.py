        # 每一行对应节点n、温位g、时段t，热流系数均采用功率口径。
        # Node-grade-time heat balances.
        for node_name, node in self.nodes.items():
            for grade_name, grade in self.grades.items():
                demand_column = node.get("demand_columns", {}).get(grade_name)
                for t in self.times:
                    row: dict[int, float] = {}
                    # 仅汇入安装在本节点且允许服务该温位的热源出力。
                    for s_name, source in self.sources.items():
                        idx = self.variables["source_heat"].get((s_name, grade_name, t))
                        if idx is not None and source["node"] == node_name:
                            self._add(row, idx, 1.0)
                    # 正反方向分开登记；管容量与方向互斥约束在此前装配。
                    for e_name, pipe in self.pipes.items():
                        forward = self.variables["pipe_flow_forward"][(e_name, grade_name, t)]
                        # 离开本节点的热流以负号计入，尚未扣除下游管损。
                        if pipe["from"] == node_name:
                            self._add(row, forward, -1.0)
                        # 到达节点的热流乘效率；接收端承担输配损失。
                        if pipe["to"] == node_name:
                            self._add(row, forward, self._pipe_efficiency(pipe, t))
                            activated = self.variables["pipe_retrofit_flow_forward"].get(
                                (e_name, grade_name, t)
                            )
                            if activated is not None:
                                gain = self._pipe_retrofit_efficiency(pipe, t) - self._pipe_efficiency(pipe, t)
                                self._add(row, activated, max(0.0, gain))
                        # 允许反向输送时使用独立变量，符号按端点反转。
                        reverse = self.variables["pipe_flow_reverse"].get((e_name, grade_name, t))
                        if reverse is not None:
                            if pipe["to"] == node_name:
                                self._add(row, reverse, -1.0)
                            if pipe["from"] == node_name:
                                self._add(row, reverse, self._pipe_efficiency(pipe, t))
                                activated_reverse = self.variables["pipe_retrofit_flow_reverse"].get(
                                    (e_name, grade_name, t)
                                )
                                if activated_reverse is not None:
                                    gain = self._pipe_retrofit_efficiency(pipe, t) - self._pipe_efficiency(pipe, t)
                                    self._add(row, activated_reverse, max(0.0, gain))
                    # 放热增加节点供给，充热占用节点供给。
                    for st_name, storage in self.storages.items():
                        if storage["node"] == node_name and storage["grade"] == grade_name:
                            self._add(row, self.variables["storage_discharge"][(st_name, t)], 1.0)
                            self._add(row, self.variables["storage_charge"][(st_name, t)], -1.0)
                    # 只有配置允许并注册失供变量时才进入等式；严格主模型可无此项。
                    unmet = self.variables["unserved"].get((node_name, grade_name, t))
                    if unmet is not None:
                        self._add(row, unmet, 1.0)
                    # 上调增加需求、下调减少需求，分别按相反符号登记。
                    up = self.variables["demand_up"].get((node_name, grade_name, t))
                    down = self.variables["demand_down"].get((node_name, grade_name, t))
                    if up is not None:
                        self._add(row, up, -1.0)
                    if down is not None:
                        self._add(row, down, 1.0)
                    # RC覆盖节点以实际交付量闭合，不能再叠加同一建筑固定负荷。
                    if self.building_thermal is not None and (node_name,grade_name) in self.building_thermal.covered_node_grades:
                        delivered = self.building_thermal.node_heat[node_name,grade_name,t]
                        self._add(row,delivered,-1.0)
                        p.add_constraint(row,lb=0.0,ub=0.0)
                        self._record_time_metric("demand_kwh",delivered,1.0,t)
                        self._record_time_metric("building_heat_delivered_kwh",delivered,1.0,t)
                        self._record_time_metric("demand_exergy_kwh",delivered,self._exergy_factor(float(grade["temperature_c"])),t)
                        reference = self._demand_value(node,demand_column,t) if demand_column else 0.0
                        self.metric_constants["building_reference_heat_kwh"] += reference*self._op_weight(t)
                        self.scenario_metric_constants[self._scenario(t)]["building_reference_heat_kwh"] += reference*self._physical_weight(t)
                        self._record_metering_reference(node_name, grade, reference, t)
                        continue
                    # 未被RC覆盖的温位沿用登记需求列及其调节接口。
                    demand = self._demand_value(node, demand_column, t) if demand_column else 0.0
                    metering = node.get("heat_metering", {})
                    existing_metering = float(
                        metering.get("existing_coverage_fraction", 0.0)
                    )
                    saving_fraction = float(
                        metering.get("saving_fraction_at_full_coverage", 0.0)
                    )
                    existing_metering_saving = (
                        demand * existing_metering * saving_fraction
                    )
                    metering_idx = self.variables["heat_metering_coverage"].get(
                        node_name
                    )
                    # 计量覆盖投资按登记节约系数调整需求，须与原需求保持同一单位。
                    if metering_idx is not None and demand > 0.0 and saving_fraction > 0.0:
                        self._add(row, metering_idx, demand * saving_fraction)
                    # 末端改造在两个温位间转移符合条件的需求，不凭空产生热量。
                    retrofit = node.get("temperature_retrofit")
                    retrofit_idx = self.variables["terminal_temperature_retrofit"].get(node_name)
                    if retrofit and retrofit_idx is not None:
                        source_grade = str(retrofit["source_grade"])
                        target_grade = str(retrofit["target_grade"])
                        source_column = node["demand_columns"][source_grade]
                        shift = (
                            float(retrofit.get("eligible_fraction", 1.0))
                            * self._demand_value(node, source_column, t)
                        )
                        if grade_name == source_grade:
                            self._add(row, retrofit_idx, shift)
                        elif grade_name == target_grade:
                            self._add(row, retrofit_idx, -shift)
                    # 固定需求分支以双侧相等界闭合；此前调节项已在系数行内。
                    effective_constant_demand = demand - existing_metering_saving
                    p.add_constraint(
                        row,
                        lb=effective_constant_demand,
                        ub=effective_constant_demand,
                    )
