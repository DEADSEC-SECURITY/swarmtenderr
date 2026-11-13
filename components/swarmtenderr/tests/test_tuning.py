"""Editable optimizer tuning is validated, durable and used by policy decisions."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import test_product as product
from swarmtenderr.app import create_app
from swarmtenderr.optimizer_settings import OptimizerSettings
from swarmtenderr.qbit import UpstreamError
from swarmtenderr.runtime import OPTIMIZER_KEYS
from swarmtenderr.store import Conflict
from swarmtenderr_policy import admission, optimizer_observability, space_planner
from swarmtenderr_policy.tuning import DEFAULT_POLICY, POLICY_DEFAULTS, Policy


class TuningTests(unittest.TestCase):
    setUp = product.ProductTests.setUp
    request = product.ProductTests.request
    add_user = product.ProductTests.add_user

    def save(self, changes, revision=None):
        return self.request('/api/v1/settings','PATCH',{'revision':self.app.runtime.public()['revision'] if revision is None else revision,'changes':changes})

    def test_save_persist_reset_and_policy_revision(self):
        first=self.app.runtime.values()
        status,saved,_=self.save({'ranking_interval_seconds':360,'min_trial_seeds':7})
        self.assertEqual(status,200,saved)
        self.assertEqual(saved['values']['min_trial_seeds'],7)
        self.assertGreater(self.app.runtime.values()['policy_revision'],first['policy_revision'])
        other=create_app(self.settings,False)
        self.assertEqual(other.runtime.values()['ranking_interval_seconds'],360)
        status,reset,_=self.request('/api/v1/settings/reset','POST',{'revision':saved['revision'],'keys':['ranking_interval_seconds']})
        self.assertEqual(status,200,reset)
        self.assertEqual(reset['values']['ranking_interval_seconds'],300)
        self.assertEqual(len(saved['optimizer_fields']),len(POLICY_DEFAULTS))
        self.assertEqual(self.save({'min_trial_seeds':8},revision=0)[0],409)

    def test_invalid_ranges_and_combined_relationships_do_not_commit(self):
        for changes in [{'swarm_trial_seconds':60},{'min_trial_seeds':0},{'probe_seconds':True},
                        {'ranking_interval_seconds':30.5},{'minimum_rate_sample_seconds':600}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.save(changes)[0],400)
                self.assertEqual(self.app.runtime.public()['revision'],0)
        self.assertEqual(self.save({'swarm_trial_seconds':90,'swarm_warmup_seconds':60,'minimum_rate_sample_seconds':60})[0],200)

    def test_external_owner_locks_standalone_controls_and_requires_observer(self):
        self.app=create_app(replace(self.settings,optimizer_state='/fixture/feed.json',optimizer_control_socket='/fixture/control.sock'),False)
        self.assertTrue(OPTIMIZER_KEYS <= set(self.app.runtime.public()['locked']))
        self.assertEqual(self.save({'ranking_interval_seconds':360})[0],403)
        with self.assertRaises(ValueError):create_app(replace(self.settings,optimizer_control_socket='/fixture/control.sock'),False)

    def test_guard_api_is_admin_only_and_checks_origin_and_csrf(self):
        self.settings=replace(self.settings,optimizer_state='/fixture/feed.json',optimizer_control_socket='/fixture/control.sock')
        self.app=create_app(self.settings,False)
        contract={'revision':123,'values':{},'fields':[]}
        with patch.object(OptimizerSettings,'request',return_value=contract) as upstream:
            self.assertEqual(self.request('/api/v1/optimizer/settings')[0],200)
            result=self.request('/api/v1/optimizer/settings','PATCH',{'revision':123,'changes':{'ranking_interval_seconds':360}})
            self.assertEqual(result[0],200,result)
            self.assertEqual(upstream.call_args.args[-1],'admin')
            self.assertEqual(self.request('/api/v1/optimizer/settings','PATCH',{},HTTP_X_CSRF_TOKEN='invalid')[0],403)
            self.assertEqual(self.request('/api/v1/optimizer/settings','PATCH',{},HTTP_ORIGIN='https://other.invalid')[0],403)
            self.add_user('viewer')
            self.token=self.app.auth.login('viewer','test-only-account-password','fixture');self.csrf=self.app.auth.csrf(self.token)
            self.assertEqual(self.request('/api/v1/optimizer/settings')[0],403)
            self.assertEqual(self.request('/api/v1/optimizer/settings','PATCH',{})[0],403)

    def test_guard_api_reports_conflict_and_unavailable_without_retry(self):
        self.app=create_app(replace(self.settings,optimizer_state='/fixture/feed.json',optimizer_control_socket='/fixture/control.sock'),False)
        for error,status in [(Conflict('reload'),409),(UpstreamError('unavailable'),502)]:
            with patch.object(OptimizerSettings,'request',side_effect=error) as upstream:
                self.assertEqual(self.request('/api/v1/optimizer/settings','PATCH',{'revision':1,'changes':{}})[0],status)
                self.assertEqual(upstream.call_count,1)


class PolicyTuningTests(unittest.TestCase):
    def test_ranking_interval_changes_decision_and_reported_deadline(self):
        policy=replace(DEFAULT_POLICY,ranking_interval_seconds=600)
        state={'queue_front':['a'],'queue_ranked_at':1000,'history':{}}
        plan={'front':['a'],'reasons':{},'swarm_trials':[]}
        self.assertTrue(admission.ranking_ready(state,plan,1301))
        self.assertFalse(admission.ranking_ready(state,plan,1301,policy=policy))
        optimizer_observability.snapshot(state,{}, {},plan,1301,policy=policy)
        self.assertEqual(state['optimizer']['next_ranking_at'],1600)

    def test_unknown_capacity_and_buffer_are_used_without_mutating_defaults(self):
        row={'amount_left':0,'size':0}
        before=space_planner.requirements(row)
        after=space_planner.requirements(row,policy=replace(DEFAULT_POLICY,unknown_size_gib=8))
        self.assertGreater(after['ssd'],before['ssd']*3)
        proposed={'front':[],'order':[]}
        plan=space_planner.plan({}, {}, {},3,proposed,100*1024**3,0,None,500*1024**3,1000,
            policy=replace(DEFAULT_POLICY,other_buffer_gib=10))
        self.assertEqual(plan['ssd_budget_bytes'],40*1024**3)
        self.assertEqual(Policy().unknown_size_gib,2)

    def test_trial_deadline_and_explanation_use_configured_window(self):
        policy=replace(DEFAULT_POLICY,swarm_trial_seconds=240)
        history={}
        plan={'front':['a'],'reasons':{'a':'Swarm discovery trial'},'swarm_trials':['a'],'seed_trial':None,'metadata_trial':None,'metadata_repair':None}
        admission.commit_plan(plan,{'a':{'hash':'a','amount_left':1000,'completed':0}},history,1000,policy=policy)
        self.assertEqual(history['a']['swarm_trial_until'],1240)
        self.assertIn('4-minute sample',optimizer_observability.reason_text('Swarm discovery trial',{'popularity_hint':0,'peer_hint':0},policy=policy))
        self.assertEqual(DEFAULT_POLICY.swarm_trial_seconds,180)
