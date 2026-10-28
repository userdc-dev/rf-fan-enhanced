import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('manager',Path(__file__).parents[1]/'custom_components/rf_fan/manager.py')
manager=importlib.util.module_from_spec(spec); spec.loader.exec_module(manager)

class CommandExchange(unittest.TestCase):
    def setUp(self):
        self.data={'frequency':433920000,'replay_mode':'raw','commands':{'speed_1':{'timings':[99,-197],'repeat':0,'b64':'sgACAAMG'}}}
    def test_roundtrip_preserves_original_packet_and_timings(self):
        original=json.dumps(self.data,sort_keys=True)
        restored=manager.import_commands(manager.export_commands(self.data),433920000)
        self.assertEqual(restored['speed_1']['timings'],self.data['commands']['speed_1']['timings'])
        self.assertEqual(restored['speed_1']['b64'],self.data['commands']['speed_1']['b64'])
        self.assertEqual(restored['speed_1']['replay_mode'],'raw')
        self.assertEqual(json.dumps(self.data,sort_keys=True),original)
    def test_corrupt_checksum_is_rejected(self):
        payload=json.loads(manager.export_commands(self.data));payload['commands']['speed_1']['timings'][0]=200
        with self.assertRaises(ValueError): manager.import_commands(json.dumps(payload),433920000)
    def test_wrong_frequency_is_rejected(self):
        with self.assertRaises(ValueError): manager.import_commands(manager.export_commands(self.data),315000000)
    def test_legacy_filter_fallback_is_preserved_across_devices(self):
        self.data.update(replay_mode='legacy',direct_capture=True)
        command=manager.import_commands(manager.export_commands(self.data),433920000)['speed_1']
        self.assertEqual(command['replay_mode'],'legacy');self.assertTrue(command['legacy_clean'])
    def test_invalid_or_empty_timings_are_rejected(self):
        for value in ([],[0,-10],[True,-10],['100',-10]):
            with self.assertRaises(ValueError):manager.validate_commands({'key':{'timings':value}})
    def test_invalid_command_names_and_modes_are_rejected(self):
        with self.assertRaises(ValueError):manager.validate_commands({'../key':{'timings':[100,-10]}})
        with self.assertRaises(ValueError):manager.validate_commands({'key':{'timings':[100,-10],'replay_mode':'unsafe'}})

if __name__=='__main__':unittest.main()
