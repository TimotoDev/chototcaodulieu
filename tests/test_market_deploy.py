import unittest
from botocore.exceptions import ClientError
from tools.deploy_market import enable_backups


class BackupReadinessTests(unittest.TestCase):
    def test_new_table_backup_readiness_is_retried_without_ignoring_other_errors(self):
        class Client:
            calls=0
            def update_continuous_backups(self,**kwargs):
                self.calls+=1
                if self.calls==1:raise ClientError({'Error':{'Code':'ContinuousBackupsUnavailableException','Message':'enabling'}},'UpdateContinuousBackups')
        client=Client();enable_backups(client,'new-table',sleep=lambda _:None)
        self.assertEqual(client.calls,2)
