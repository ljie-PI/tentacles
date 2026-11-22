"""Offline checks for explicit model providers and pipeline semantic filter instructions."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from tentacles.llm.config import Defaults, ModelSpec
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.net import HttpPool
from tentacles.personas.models import InterestTag, PersonaView
from tentacles.pipelines.config import SemanticSpec
from tentacles.pipelines.decision import score_decisions
from tentacles.pipelines.llm import score_llm
from tentacles.pipelines.models import StageBatch

GO_ENDPOINT = 'https://opencode.ai/inference/go/openai/v1'
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)


class ProviderConfigTests(unittest.TestCase):
    def test_provider_endpoints_and_explicit_openai_compatible_base_url(self):
        go = ModelSpec(provider='opencode-go', model='deepseek-v4.1-flash')
        self.assertEqual(go.endpoint, GO_ENDPOINT)
        self.assertTrue(go.is_opencode_go)

        direct = ModelSpec(provider='deepseek', model='deepseek-flash')
        self.assertEqual(direct.endpoint, 'https://api.deepseek.com')

        generic = ModelSpec(provider='openai-compatible', model='custom-model',
                            base_url='https://inference.example.test/v1/')
        self.assertEqual(generic.endpoint, 'https://inference.example.test/v1')

        with self.assertRaisesRegex(ValidationError, 'explicit base_url'):
            ModelSpec(provider='openai-compatible', model='custom-model')

        self.assertEqual(ModelSpec(provider='openai', model='test-model').endpoint,
                         'https://api.openai.com/v1')


class ProviderAndScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_and_generic_chat_use_openai_wire_format(self):
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            body = json.loads(request.content)
            self.assertEqual(request.headers['authorization'], 'Bearer test-key')
            self.assertEqual(body['messages'][0], {'role': 'system', 'content': 'test system'})
            return httpx.Response(200, json={
                'model': body['model'],
                'choices': [{'message': {'content': 'ok'}}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
            })

        async with HttpPool(httpx.MockTransport(respond)) as pool:
            specs = {
                'direct': ModelSpec(provider='deepseek', model='deepseek-flash', api_key='test-key'),
                'generic': ModelSpec(provider='openai-compatible', model='custom-model',
                                     base_url='https://inference.example.test/v1', api_key='test-key'),
                'go': ModelSpec(provider='opencode-go', model='deepseek-v4.1-flash', api_key='test-key'),
            }
            models = ModelRegistry(specs, Defaults(), pool, clock=lambda: NOW)
            for name in specs:
                result = await models.chat(name).complete([{'role': 'user', 'content': 'test'}],
                                                          system='test system')
                self.assertEqual(result.text, 'ok')
        self.assertEqual([str(request.url) for request in requests], [
            'https://api.deepseek.com/chat/completions',
            'https://inference.example.test/v1/chat/completions',
            f'{GO_ENDPOINT}/chat/completions',
        ])
        self.assertIn('x-opencode-session', requests[2].headers)

    async def test_semantic_instructions_reach_chat_and_decision_executors(self):
        instructions = '报告主题：金融。只保留金融与宏观内容。'
        persona = PersonaView(
            name='reader', version=1, profile='关注 AI 工程与金融研究。',
            tags=[InterestTag(tag='宏观金融', description='利率与经济数据')],
            source='seed', created_at=NOW,
        )
        item = Item(source_id='mail-finance', kind='email', title='利率数据', fetched_at=NOW)

        chat_payloads = []

        def respond_chat(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            payload = json.loads(body['messages'][-1]['content'])
            chat_payloads.append(payload['persona'])
            return httpx.Response(200, json={
                'model': body['model'],
                'choices': [{'message': {'content': json.dumps([
                    {'id': 1, 'tag_ids': [1], 'score': 0.9, 'match_points': ['利率数据']}
                ], ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 5},
            })

        async with HttpPool(httpx.MockTransport(respond_chat)) as pool:
            models = ModelRegistry({'fast': ModelSpec(provider='opencode-go', model='deepseek-v4.1-flash',
                                                      api_key='test-key')},
                                   Defaults(), pool, clock=lambda: NOW)
            stage = SemanticSpec(model='fast', instructions=instructions)
            progress = StageBatch([])
            await score_llm([item], persona, stage, 0, models, 'fast', progress)
            self.assertEqual(len(progress.scores), 1)
            self.assertEqual(progress.scores[0].score, 0.9)
        self.assertEqual(len(chat_payloads), 1)
        self.assertIn(instructions, chat_payloads[0])

        decision_requests = []

        def respond_decision(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertIn(instructions, body['state']['persona'])
            decision_requests.append(request)
            return httpx.Response(200, json={
                'model': body['model'],
                'answers': {name: {'type': 'noul', 'noul': 0.9} if name.startswith('r_') or name == 'relevance'
                            else {'type': 'choice', 'choice': 't0'}
                            for name in body['questions']},
                'usage': {'input_tokens': 1, 'output_tokens': 1},
            })

        async with HttpPool(httpx.MockTransport(respond_decision)) as pool:
            models = ModelRegistry({'jev': ModelSpec(provider='openrouter', kind='decision',
                                    model='test-decision', api_key='test-key')},
                                   Defaults(), pool, clock=lambda: NOW)
            for mode in ('packed_questions', 'windowed_pool'):
                stage = SemanticSpec(model='jev', instructions=instructions, jev_batch_mode=mode)
                progress = StageBatch([])
                await score_decisions([item], persona, stage, 0, models, 'jev', progress)
                self.assertIsNone(progress.error)
                self.assertEqual(len(progress.scores), 1)
                self.assertEqual(progress.scores[0].score, 0.9)
        self.assertGreaterEqual(len(decision_requests), 2)


if __name__ == '__main__':
    unittest.main()
