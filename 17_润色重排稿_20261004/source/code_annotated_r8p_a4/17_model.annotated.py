            # 仅显式启用且候选容量为正时装配此块。
            if source.get("unit_commitment", False) and maximum_capacity > 0:
                # 读入最低出力比例与上下爬坡比例。
                min_fraction = float(source.get("minimum_output_fraction", 0.0))
                ramp_up = float(source.get("ramp_up_fraction_per_hour", 1.0))
                ramp_down = float(source.get("ramp_down_fraction_per_hour", 1.0))
                cap_idx = self.variables["source_capacity"].get(s_name)
                for t in self.times:
                    # 每时段只给开机状态添加整数身份。
                    on = p.add_var(f"source_on[{s_name},{t}]", ub=1.0, integer=True)
                    # Startup and shutdown are exact transition indicators once
                    # the adjacent on/off states are binary.  Keeping these two
                    # auxiliaries continuous removes two thirds of the unit-
                    # commitment integer variables without relaxing the model;
                    # the linking constraints below uniquely force them to 0/1.
                    # 启停事件为连续变量，相邻二元状态将其唯一锁定。
                    startup = p.add_var(f"source_startup[{s_name},{t}]", ub=1.0)
                    shutdown = p.add_var(f"source_shutdown[{s_name},{t}]", ub=1.0)
                    self.variables["source_on"][(s_name, t)] = on
                    self.variables["source_startup"][(s_name, t)] = startup
                    self.variables["source_shutdown"][(s_name, t)] = shutdown
                    # 登记的热电联产最小电出力随开机状态生效。
                    if t in minimum_electricity_by_time:
                        p.add_constraint(
                            {
                                self.variables["chp_electricity_output"][(s_name, t)]: 1.0,
                                on: -minimum_electricity_by_time[t],
                            },
                            lb=0.0,
                        )
                    # 启停费用按事件口径入账，避免按普通功率计费。
                    self._record_time_component(
                        "startup_shutdown_cost", startup, float(source.get("startup_cost_yuan", 0.0)), t, event=True
                    )
                    self._record_time_component(
                        "startup_shutdown_cost", shutdown, float(source.get("shutdown_cost_yuan", 0.0)), t, event=True
                    )

                    # 各温位热出力合计受开机状态与最大候选容量限制。
                    upper_on = {idx: 1.0 for idx in heat_by_time[t]}
                    self._add(upper_on, on, -maximum_capacity)
                    p.add_constraint(upper_on, ub=0.0)

                    # 启用最低出力时，把新增容量系数接入线性下界。
                    if min_fraction > 0:
                        lower_on = {idx: 1.0 for idx in heat_by_time[t]}
                        if cap_idx is not None:
                            self._add(lower_on, cap_idx, -min_fraction)
                        self._add(lower_on, on, -maximum_capacity)
                        p.add_constraint(lower_on, lb=min_fraction * existing_capacity - maximum_capacity)

                for t in self.times:
                    # 前驱来自已登记时序边界，本段据此连接相邻状态。
                    prev = self.time_predecessor[t]
                    on = self.variables["source_on"][(s_name, t)]
                    prev_on = self.variables["source_on"][(s_name, prev)]
                    startup = self.variables["source_startup"][(s_name, t)]
                    shutdown = self.variables["source_shutdown"][(s_name, t)]
                    # Exact three-row state-transition hull.  The two sum
                    # inequalities imply all four individual upper links
                    # (startup <= on, startup <= 1-prev, shutdown <= prev,
                    # shutdown <= 1-on) when combined with the equality.  This
                    # is the same tight relaxation with two fewer rows per hour.
                    # A one-row cyclic group has prev_on == on. Accumulate
                    # coefficients so its transition cancels exactly.
                    # 转换等式为启动减停机等于当前开机减前驱开机。
                    transition = {startup: 1.0, shutdown: -1.0, on: -1.0}
                    # 累加同一索引系数，单时段循环的状态项正确抵消。
                    self._add(transition, prev_on, 1.0)
                    p.add_constraint(transition, lb=0.0, ub=0.0)
                    # 第一条和约束限制事件之和不超过两期开机之和。
                    active_sum = {startup: 1.0, shutdown: 1.0, on: -1.0}
                    self._add(active_sum, prev_on, -1.0)
                    p.add_constraint(active_sum, ub=0.0)
                    # 第二条和约束补上关机侧界，合计三行保留紧松弛。
                    inactive_sum = {startup: 1.0, shutdown: 1.0, on: 1.0}
                    self._add(inactive_sum, prev_on, 1.0)
                    p.add_constraint(inactive_sum, ub=2.0)

                    # 上爬坡关联装机容量；启动事件提供候选最大容量余量。
                    ramp_up_row = {idx: 1.0 for idx in heat_by_time[t]}
                    for idx in heat_by_time[prev]:
                        self._add(ramp_up_row, idx, -1.0)
                    if cap_idx is not None:
                        self._add(ramp_up_row, cap_idx, -ramp_up)
                    self._add(ramp_up_row, startup, -maximum_capacity)
                    p.add_constraint(ramp_up_row, ub=ramp_up * existing_capacity)

                    # 下爬坡对称处理，停机事件提供相应变化余量。
                    ramp_down_row = {idx: 1.0 for idx in heat_by_time[prev]}
                    for idx in heat_by_time[t]:
                        self._add(ramp_down_row, idx, -1.0)
                    if cap_idx is not None:
                        self._add(ramp_down_row, cap_idx, -ramp_down)
                    self._add(ramp_down_row, shutdown, -maximum_capacity)
                    p.add_constraint(ramp_down_row, ub=ramp_down * existing_capacity)
