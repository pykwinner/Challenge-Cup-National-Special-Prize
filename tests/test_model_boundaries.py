from pathlib import Path
import copy
import unittest

from heatplan.io import load_inputs
from heatplan.model import HeatPlanningModel


ROOT = Path(__file__).resolve().parents[1]


class ModelBoundaryTest(unittest.TestCase):
    def test_zero_heat_without_cooling_or_renewables_initializes_metrics(self):
        config, frame = load_inputs(
            ROOT / "data/example_city.yaml",
            ROOT / "data/example_timeseries.csv",
        )
        config = copy.deepcopy(config)
        config.pop("multi_energy_services", None)
        config.pop("renewable_electricity", None)
        for node in config["nodes"]:
            for column in node.get("demand_columns", {}).values():
                frame[column] = 0.0

        model = HeatPlanningModel(config, frame)
        self.assertEqual(model.metric_constants.get("demand_kwh", 0.0), 0.0)

        model.build()

        # Joint multistage planning reads this mapping directly after build.
        stage_demand = model.metric_constants.get("demand_kwh", 0.0)
        self.assertEqual(stage_demand, 0.0)
        self.assertEqual(model.metric_constants.get("cooling_demand_kwh", 0.0), 0.0)
        self.assertEqual(
            model.metric_constants.get("renewable_electricity_available_kwh", 0.0),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
