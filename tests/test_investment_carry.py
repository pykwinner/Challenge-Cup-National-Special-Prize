import copy
from types import SimpleNamespace
import unittest

import pandas as pd

from heatplan.chronology import investment_freeze_audit
from heatplan.experiments import carry_forward_investments, freeze_investment_plan


class InvestmentCarryTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "sources": [],
            "storages": [],
            "pipes": [],
            "nodes": [],
            "planning": {"risk": {"measure": "cvar"}},
            "policy": {"maximum_lcoh_yuan_per_kwh": 0.5},
            "biomass_supply_chains": [
                {
                    "name": "pellet_chain",
                    "existing_inventory_capacity_kg": 100.0,
                    "max_new_inventory_capacity_kg": 50.0,
                },
                {
                    "name": "missing_chain",
                    "existing_inventory_capacity_kg": 25.0,
                    "max_new_inventory_capacity_kg": 75.0,
                },
            ],
            "microgrid_islanding": {
                "existing_renewable_capacity_kw": 10.0,
                "max_new_renewable_capacity_kw": 40.0,
                "existing_battery_power_kw": 5.0,
                "max_new_battery_power_kw": 20.0,
                "existing_battery_energy_kwh": 30.0,
                "max_new_battery_energy_kwh": 60.0,
                "existing_backup_capacity_kw": 8.0,
                "max_new_backup_capacity_kw": 12.0,
            },
        }

    @staticmethod
    def artifacts(rows):
        return SimpleNamespace(investments=pd.DataFrame(rows))

    def test_carries_biomass_inventory_and_all_microgrid_investments(self):
        original = copy.deepcopy(self.config)
        artifacts = self.artifacts(
            [
                {
                    "category": "biomass_inventory_capacity",
                    "asset": "pellet_chain",
                    "option": "new",
                    "value": 80.0,
                },
                {
                    "category": "microgrid_renewable_capacity",
                    "asset": "system",
                    "option": "new",
                    "value": 15.0,
                },
                {
                    "category": "microgrid_battery_power",
                    "asset": "system",
                    "option": "new",
                    "value": 7.0,
                },
                {
                    "category": "microgrid_battery_energy",
                    "asset": "system",
                    "option": "new",
                    "value": 70.0,
                },
                {
                    "category": "microgrid_backup_capacity",
                    "asset": "system",
                    "option": "new",
                    "value": 3.0,
                },
            ]
        )

        carried = carry_forward_investments(self.config, artifacts)

        self.assertEqual(self.config, original)
        pellet = carried["biomass_supply_chains"][0]
        self.assertEqual(pellet["existing_inventory_capacity_kg"], 180.0)
        self.assertEqual(pellet["max_new_inventory_capacity_kg"], 0.0)
        microgrid = carried["microgrid_islanding"]
        self.assertEqual(microgrid["existing_renewable_capacity_kw"], 25.0)
        self.assertEqual(microgrid["max_new_renewable_capacity_kw"], 25.0)
        self.assertEqual(microgrid["existing_battery_power_kw"], 12.0)
        self.assertEqual(microgrid["max_new_battery_power_kw"], 13.0)
        self.assertEqual(microgrid["existing_battery_energy_kwh"], 100.0)
        self.assertEqual(microgrid["max_new_battery_energy_kwh"], 0.0)
        self.assertEqual(microgrid["existing_backup_capacity_kw"], 11.0)
        self.assertEqual(microgrid["max_new_backup_capacity_kw"], 9.0)

    def test_missing_investment_rows_leave_asset_limits_unchanged(self):
        artifacts = self.artifacts(
            [
                {
                    "category": "biomass_inventory_capacity",
                    "asset": "pellet_chain",
                    "option": "new",
                    "value": 10.0,
                }
            ]
        )

        carried = carry_forward_investments(self.config, artifacts)

        self.assertEqual(
            carried["biomass_supply_chains"][1],
            self.config["biomass_supply_chains"][1],
        )
        self.assertEqual(
            carried["microgrid_islanding"],
            self.config["microgrid_islanding"],
        )

    def test_freeze_audit_checks_biomass_and_microgrid_installed_values(self):
        artifacts = self.artifacts(
            [
                {
                    "category": "biomass_inventory_capacity",
                    "asset": "pellet_chain",
                    "option": "new",
                    "value": 20.0,
                },
                {
                    "category": "microgrid_renewable_capacity",
                    "asset": "system",
                    "option": "new",
                    "value": 15.0,
                },
                {
                    "category": "microgrid_battery_power",
                    "asset": "system",
                    "option": "new",
                    "value": 7.0,
                },
                {
                    "category": "microgrid_battery_energy",
                    "asset": "system",
                    "option": "new",
                    "value": 25.0,
                },
                {
                    "category": "microgrid_backup_capacity",
                    "asset": "system",
                    "option": "new",
                    "value": 3.0,
                },
            ]
        )

        fixed = freeze_investment_plan(
            self.config, artifacts, relax_reliability=False
        )
        audit = investment_freeze_audit(self.config, fixed, artifacts)

        self.assertTrue(bool(audit["freeze_ok"].all()))
        self.assertTrue((audit["remaining_candidate_limit"] == 0.0).all())
        checked = audit[audit["expected_frozen_value"].notna()]
        self.assertTrue(
            (
                checked["expected_frozen_value"]
                - checked["frozen_installed_or_fixed_value"]
            ).abs().lt(1e-9).all()
        )


if __name__ == "__main__":
    unittest.main()
