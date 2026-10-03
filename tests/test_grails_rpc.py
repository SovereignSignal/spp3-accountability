import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import grails_chain as C


class TestReadRetries(unittest.TestCase):
    @patch.object(C.time, 'sleep')
    def test_recovers_after_transport_failure(self, sleep):
        client = Mock()
        client._rpc.side_effect = [RuntimeError('transport failure'), {'result': 1}]
        self.assertEqual(C.read_with_retry(client, 'eth_chainId', []), {'result': 1})
        self.assertEqual(client._rpc.call_count, 2)
        self.assertIn(unittest.mock.call(1), sleep.call_args_list)

    @patch.object(C.time, 'sleep')
    def test_retry_budget_is_finite(self, sleep):
        client = Mock()
        client._rpc.side_effect = RuntimeError('transport failure')
        with self.assertRaises(RuntimeError):
            C.read_with_retry(client, 'eth_getLogs', [])
        self.assertEqual(client._rpc.call_count, 4)

    @patch.object(C.time, 'sleep')
    def test_validation_error_is_not_retried(self, sleep):
        client = Mock()
        client._rpc.side_effect = ValueError('invalid data')
        with self.assertRaises(ValueError):
            C.read_with_retry(client, 'eth_getLogs', [])
        self.assertEqual(client._rpc.call_count, 1)


if __name__ == '__main__':
    unittest.main()
