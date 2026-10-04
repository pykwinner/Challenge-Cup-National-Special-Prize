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
