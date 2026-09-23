import io
import json
import unittest
import time
from unittest.mock import MagicMock
from botocore.exceptions import ClientError
from reviewer import Reviewer, model_evidence, validate


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.r = Reviewer.__new__(Reviewer)
        self.r.table, self.r.bucket, self.r.model = 'table', 'bucket', 'model'
        self.r.db, self.r.s3, self.r.model_client = MagicMock(), MagicMock(), MagicMock()
        self.item = {'event_count': 2, 'evidence_key': 'agent-evidence/staging/events/e.json'}
        self.r.db.update_item.return_value = {'Attributes': self.item}
        self.evidence = {'incident_id': 'abc', 'event': {'service': 'timoto-backend',
            'environment': 'staging', 'event': 'api_exception', 'message': 'secret-canary',
            'log_stream': 'ecs/smoke/test', 'stack_frames': []}}
        self.r.s3.get_object.return_value = {'Body': io.BytesIO(json.dumps(self.evidence).encode())}
        self.report = {'summary': 'Observed exception', 'hypotheses': ['Unknown cause'],
                       'next_checks': ['Inspect matching source'], 'confidence': 'low'}
        self.r.model_client.converse.return_value = {'stopReason': 'end_turn',
            'output': {'message': {'content': [{'text': json.dumps(self.report)}]}}}

    def test_review_and_report(self):
        self.assertEqual(self.r.review('incident#abc'), 'reviewed')
        payload = json.loads(self.r.s3.put_object.call_args.kwargs['Body'])
        self.assertTrue(payload['advisory_only'])
        self.assertTrue(payload['synthetic'])
        self.assertEqual(payload['observed']['unique_exception_events'], 2)
        self.assertNotIn('secret-canary', str(self.r.model_client.converse.call_args))
        self.assertIn('Root cause and user impact are not established', payload['summary'])
        self.assertFalse(payload['hypotheses_verified'])
        self.assertEqual(payload['confidence'], 'low')

    def test_unchanged_does_not_call_model(self):
        self.item['reviewed_count'] = 2
        self.assertEqual(self.r.review('incident#abc'), 'unchanged')
        self.r.model_client.converse.assert_not_called()

    def test_attempt_limit(self):
        self.item['review_attempts'] = 3
        self.assertEqual(self.r.review('incident#abc'), 'limited')
        self.r.model_client.converse.assert_not_called()

    def test_cooldown_defers_new_events(self):
        self.item['last_review_at'] = int(time.time())
        self.assertEqual(self.r.review('incident#abc'), 'cooldown')
        self.r.model_client.converse.assert_not_called()

    def test_wrong_evidence_incident_rejected(self):
        self.evidence['incident_id'] = 'different'
        self.r.s3.get_object.return_value = {'Body': io.BytesIO(json.dumps(self.evidence).encode())}
        with self.assertRaises(ValueError):
            self.r.review('incident#abc')
        self.r.model_client.converse.assert_not_called()

    def test_lease_contention(self):
        self.r.db.update_item.side_effect = ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem')
        self.assertEqual(self.r.review('incident#abc'), 'leased')
        self.r.model_client.converse.assert_not_called()

    def test_budget_exhaustion(self):
        self.r.db.update_item.side_effect = [{'Attributes': self.item},
            ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem'), {}]
        self.assertEqual(self.r.review('incident#abc'), 'budget')
        self.r.model_client.converse.assert_not_called()

    def test_invalid_model_output_retries(self):
        self.r.model_client.converse.return_value['stopReason'] = 'max_tokens'
        with self.assertRaises(ValueError):
            self.r.review('incident#abc')
        self.r.s3.put_object.assert_not_called()
        self.assertIn('RETRY_PENDING', self.r.db.update_item.call_args.kwargs['ExpressionAttributeValues'].values())

    def test_storage_failure_not_marked_reviewed(self):
        self.r.s3.put_object.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            self.r.review('incident#abc')
        self.assertIn('RETRY_PENDING', self.r.db.update_item.call_args.kwargs['ExpressionAttributeValues'].values())

    def test_production_rejected(self):
        self.evidence['event']['environment'] = 'production'
        with self.assertRaises(ValueError):
            model_evidence(self.evidence, 1)

    def test_invalid_schema_rejected(self):
        with self.assertRaises(ValueError):
            validate('{"command":"deploy"}')


if __name__ == '__main__':
    unittest.main()
