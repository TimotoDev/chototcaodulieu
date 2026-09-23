import json
import os
import unittest
from unittest.mock import MagicMock, patch

import pipeline


class IntakeTests(unittest.TestCase):
    def sample(self, kind='api_exception'):
        raw = {'schema_version': 1, 'service': 'timoto-backend', 'environment': 'staging', 'event': kind,
               'status': 500, 'release_sha': '123abc', 'route': '/api/test/', 'error_fingerprint': 'fp',
               'message': 'Bearer secret-canary', 'token': 'secret-canary', 'stack_frames': []}
        return pipeline.normalize(raw, {'logGroup': '/ecs/timoto-backend-fargate', 'logStream': 'smoke'}, {'id': 'event-id', 'timestamp': 1700000000000})

    def test_arbitrary_text_and_secrets_not_forwarded(self):
        clean = self.sample()
        self.assertNotIn('secret-canary', json.dumps(clean))
        self.assertNotIn('token', clean)

    @patch.dict(os.environ, {'TABLE_NAME': 'test', 'BUCKET': 'test'})
    def test_duplicate_does_not_increment_incident(self):
        db, s3 = MagicMock(), MagicMock()
        db.get_item.return_value = {'Item': {'pk': {'S': 'exists'}}}
        self.assertEqual(pipeline.collect_one(self.sample(), db, s3), 'duplicate')
        db.transact_write_items.assert_not_called()
        s3.put_object.assert_not_called()

    @patch.dict(os.environ, {'TABLE_NAME': 'test', 'BUCKET': 'test'})
    def test_request_summary_does_not_open_second_incident(self):
        db, s3 = MagicMock(), MagicMock()
        db.get_item.return_value = {}
        pipeline.collect_one(self.sample('api_request'), db, s3)
        self.assertEqual(len(db.transact_write_items.call_args.kwargs['TransactItems']), 1)

    @patch.dict(os.environ, {'TABLE_NAME': 'test', 'BUCKET': 'test'})
    def test_storage_failure_leaves_event_retryable(self):
        db, s3 = MagicMock(), MagicMock()
        db.get_item.return_value = {}
        s3.put_object.side_effect = RuntimeError('storage unavailable')
        with self.assertRaises(RuntimeError):
            pipeline.collect_one(self.sample(), db, s3)
        db.transact_write_items.assert_not_called()

    @patch.dict(os.environ, {'TABLE_NAME': 'test', 'BUCKET': 'test'})
    def test_exception_enqueues_review_in_same_transaction(self):
        db, s3 = MagicMock(), MagicMock()
        db.get_item.return_value = {}
        pipeline.collect_one(self.sample(), db, s3)
        update = db.transact_write_items.call_args.kwargs['TransactItems'][1]['Update']
        self.assertIn('review_due = :now', update['UpdateExpression'])
        self.assertEqual(update['ExpressionAttributeValues'][':partition'], {'S': 'staging'})

    @patch('pipeline.boto3.client')
    @patch('pipeline.collect_one')
    def test_partial_batch_retry(self, collect_one, client):
        collect_one.side_effect = ['collected', RuntimeError('fail')]
        result = pipeline.collect({'Records': [{'messageId': 'ok', 'body': '{}'}, {'messageId': 'failed', 'body': '{}'}]}, None)
        self.assertEqual(result, {'batchItemFailures': [{'itemIdentifier': 'failed'}]})


if __name__ == '__main__':
    unittest.main()
