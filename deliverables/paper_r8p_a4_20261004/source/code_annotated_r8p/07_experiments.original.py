def freeze_investment_plan(
    config: dict,
    nominal,
    *,
    relax_reliability: bool = False,
) -> dict:
    """Convert an optimized plan into fixed existing assets for replay.

    Reliability is retained by default. A diagnostic stress test may explicitly
    pass ``relax_reliability=True`` to quantify shortage, but that relaxed run
    is not evidence of compliance with the original service requirement.
    """
    fixed = copy.deepcopy(config)
    fixed["replay_service_policy"] = (
        "diagnostic_relaxed_reliability" if relax_reliability else "original_service_requirements"
    )
    # A frozen asset has no *new* investment variable, but its selected annual
    # capital charge still belongs in annual replay and affordability tests.
    planning_summary = getattr(nominal, "summary", {})
    components = planning_summary.get("components_yuan_per_year", {})
    fixed["fixed_investment_accounting"] = {
        "scope": "annual_selected_plan_charges",
        "components": {name: float(components.get(name, 0.0)) for name in ("annualized_capex", "fixed_om_cost")},
        "metrics": {name: float(planning_summary.get(name, 0.0)) for name in (
            "regulated_annualized_capex_yuan_per_year", "shared_non_heating_capex_yuan_per_year", "public_subsidy_yuan_per_year")},
    }
    if not hasattr(nominal, "summary"):
        # Investment-only callers can still inspect installed assets. A model
        # replay must refuse these unknown charges instead of assuming zero.
        fixed["fixed_investment_accounting"] = {"scope": "unavailable_no_planning_summary"}
    for source in fixed["sources"]:
        row = nominal.investments[
            (nominal.investments["category"] == "source_capacity")
            & (nominal.investments["asset"] == source["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        retired = nominal.investments[
            (nominal.investments["category"] == "source_retirement")
            & (nominal.investments["asset"] == source["name"])
        ]
        retirement = float(retired["value"].iloc[0]) if not retired.empty else 0.0
        source["existing_capacity_kw"] = float(source.get("existing_capacity_kw", 0.0)) + addition - retirement
        source["max_new_capacity_kw"] = 0.0
        source["max_retire_capacity_kw"] = 0.0

    for storage in fixed["storages"]:
        row = nominal.investments[
            (nominal.investments["category"] == "storage_capacity")
            & (nominal.investments["asset"] == storage["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        storage["existing_capacity_kwh"] = float(storage.get("existing_capacity_kwh", 0.0)) + addition
        storage["max_new_capacity_kwh"] = 0.0

    for pipe in fixed["pipes"]:
        retrofit_row = nominal.investments[
            (nominal.investments["category"] == "pipe_retrofit")
            & (nominal.investments["asset"] == pipe["name"])
        ]
        if pipe.get("retrofit"):
            pipe["retrofit"]["fixed_value"] = (
                float(retrofit_row["value"].iloc[0]) if not retrofit_row.empty else 0.0
            )
            pipe["retrofit"]["capex_total_yuan"] = 0.0
        selected = nominal.investments[
            (nominal.investments["category"] == "pipe_choice")
            & (nominal.investments["asset"] == pipe["name"])
            & (nominal.investments["value"] > 0.5)
        ]
        if not selected.empty:
            option_name = str(selected["option"].iloc[0])
            option = next(x for x in pipe.get("diameter_options", []) if str(x["name"]) == option_name)
            pipe["existing_capacity_kw"] = float(pipe.get("existing_capacity_kw", 0.0)) + float(option["capacity_kw"])
        pipe["diameter_options"] = []

    for node in fixed["nodes"]:
        metering = node.get("heat_metering")
        if metering:
            row = nominal.investments[
                (nominal.investments["category"] == "heat_metering_coverage")
                & (nominal.investments["asset"] == node["name"])
            ]
            addition = float(row["value"].iloc[0]) if not row.empty else 0.0
            metering["existing_coverage_fraction"] = min(
                1.0,
                float(metering.get("existing_coverage_fraction", 0.0))
                + addition,
            )
            metering["max_new_coverage_fraction"] = 0.0
            metering.pop("fixed_new_coverage_fraction", None)
            metering["capex_yuan_per_coverage_fraction"] = 0.0
        retrofit = node.get("temperature_retrofit")
        if not retrofit:
            continue
        row = nominal.investments[
            (nominal.investments["category"] == "terminal_temperature_retrofit")
            & (nominal.investments["asset"] == node["name"])
        ]
        retrofit["fixed_value"] = float(row["value"].iloc[0]) if not row.empty else 0.0
        retrofit["capex_total_yuan"] = 0.0

    grid = fixed.get("grid_connection")
    if grid:
        row = nominal.investments[nominal.investments["category"] == "grid_connection_capacity"]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        grid["existing_capacity_kw"] = float(grid.get("existing_capacity_kw", 0.0)) + addition
        grid["max_new_capacity_kw"] = 0.0
    for chain in fixed.get("biomass_supply_chains", []):
        row = nominal.investments[
            (nominal.investments["category"] == "biomass_inventory_capacity")
            & (nominal.investments["asset"] == chain["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        chain["existing_inventory_capacity_kg"] = float(
            chain.get("existing_inventory_capacity_kg", 0.0)
        ) + addition
        chain["max_new_inventory_capacity_kg"] = 0.0
    microgrid = fixed.get("microgrid_islanding")
    if microgrid:
        fields = {
            "microgrid_renewable_capacity": ("existing_renewable_capacity_kw", "max_new_renewable_capacity_kw"),
            "microgrid_battery_power": ("existing_battery_power_kw", "max_new_battery_power_kw"),
            "microgrid_battery_energy": ("existing_battery_energy_kwh", "max_new_battery_energy_kwh"),
            "microgrid_backup_capacity": ("existing_backup_capacity_kw", "max_new_backup_capacity_kw"),
        }
        for category, (existing_key, max_key) in fields.items():
            row = nominal.investments[nominal.investments["category"] == category]
            addition = float(row["value"].iloc[0]) if not row.empty else 0.0
            microgrid[existing_key] = float(microgrid.get(existing_key, 0.0)) + addition
            microgrid[max_key] = 0.0
    if relax_reliability:
        fixed["planning"]["max_unserved_rate"] = 1.0
        fixed["planning"]["max_scenario_unserved_rate"] = 1.0
        fixed["planning"]["firm_capacity_reserve_margin"] = 0.0
    return fixed
