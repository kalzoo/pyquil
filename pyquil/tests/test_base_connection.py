import pytest
import os
import requests_mock
from configparser import ConfigParser
from pyquil.api._base_connection import ForestSession
from pyquil.api._config import PyquilConfig
import urllib.parse


def fixture_path(path: str) -> str:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(dir_path, 'data', path)


test_config_paths = {
    'QCS_CONFIG': fixture_path('qcs_config.test'),
    'FOREST_CONFIG': fixture_path('forest_config.test'),
}


def test_forest_session_request_authenticated_with_user_token():
    config = PyquilConfig(test_config_paths)
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'qmi_auth_token_path', fixture_path('qmi_auth_token_invalid.json'))
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'user_auth_token_path', fixture_path('user_auth_token_valid.json'))
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'url', 'mock://forest')
    config._parse_auth_tokens()

    session = ForestSession(config)
    mock_adapter = requests_mock.Adapter()
    session.mount('mock', mock_adapter)

    url = '%s/devices' % config.forest_url
    headers = {
        # access token from ./data/user_auth_token_valid.json.
        'Authorization': 'Bearer secret'
    }
    mock_adapter.register_uri('GET', url, status_code=200, json=[{'id': 0}], headers=headers)

    devices = session.get(url).json()
    assert len(devices) == 1
    assert devices[0]['id'] == 0


def test_forest_session_request_authenticated_with_qmi_auth():
    config = PyquilConfig(test_config_paths)
    config.configparsers['QCS_CONFIG'].set(
        'Rigetti Forest', 'qmi_auth_token_path',
        fixture_path('qmi_auth_token_valid.json'))
    config.configparsers['QCS_CONFIG'].set(
        'Rigetti Forest', 'user_auth_token_path',
        fixture_path('user_auth_token_invalid.json'))
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'url', 'mock://forest')
    config._parse_auth_tokens()

    session = ForestSession(config)
    mock_adapter = requests_mock.Adapter()
    session.mount('mock', mock_adapter)

    url = '%s/devices' % config.forest_url
    headers = {
        # access token from ./data/qmi_auth_token_valid.json.
        'X-QMI-AUTH-TOKEN': 'secret'
    }
    mock_adapter.register_uri('GET', url, status_code=200, json=[{'id': 0}], headers=headers)

    devices = session.get(url).json()
    assert len(devices) == 1
    assert devices[0]['id'] == 0


def test_forest_session_request_refresh_user_auth_token():
    config = PyquilConfig(test_config_paths)
    config.configparsers['QCS_CONFIG'].set(
        'Rigetti Forest', 'qmi_auth_token_path',
        fixture_path('qmi_auth_token_invalid.json'))
    config.configparsers['QCS_CONFIG'].set(
        'Rigetti Forest', 'user_auth_token_path',
        fixture_path('user_auth_token_valid.json'))
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'url', 'mock://forest')
    config._parse_auth_tokens()

    session = ForestSession(config)
    mock_adapter = requests_mock.Adapter()
    session.mount('mock', mock_adapter)

    url = '%s/devices' % config.forest_url
    response_list = [
        # access token from ./data/user_auth_token_valid.json.
        {'status_code': 401, 'json': {'error': 'user_unauthorized'}, 'headers': {'Authorization': 'Bearer secret'}},
        # access token from new_user_auth_token.
        {'status_code': 200, 'json': [{'id': 0}], 'headers': {'Authorization': 'Bearer secret2'}},
    ]
    mock_adapter.register_uri('GET', url, response_list=response_list)

    refresh_url = '%s/auth/idp/oauth2/v1/token' % config.forest_url

    def refresh_matcher(request):
        body = dict(urllib.parse.parse_qsl(request.text))
        return (body['refresh_token'] == 'supersecret') and (body['grant_type'] == 'refresh_token')
    new_user_auth_token = {'access_token': 'secret2', 'refresh_token': 'supersecret2', 'scope': 'openid offline_access profile'}
    mock_adapter.register_uri('POST', refresh_url, status_code=200, json=new_user_auth_token, additional_matcher=refresh_matcher)

    # refresh will write the new auth tokens to file. Do not over-write text fixture data.
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'qmi_auth_token_path', '/tmp/qmi_auth_token_invalid.json')
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'user_auth_token_path', '/tmp/user_auth_token_valid.json')
    devices = session.get(url).json()
    assert len(devices) == 1
    assert devices[0]['id'] == 0


def test_forest_session_request_refresh_qmi_auth_token():
    config = PyquilConfig(test_config_paths)
    config.configparsers['QCS_CONFIG'].set(
        'Rigetti Forest', 'qmi_auth_token_path',
        fixture_path('qmi_auth_token_valid.json'))
    config.configparsers['QCS_CONFIG'].set(
        'Rigetti Forest', 'user_auth_token_path',
        fixture_path('user_auth_token_invalid.json'))
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'url', 'mock://forest')
    config._parse_auth_tokens()

    session = ForestSession(config)
    mock_adapter = requests_mock.Adapter()
    session.mount('mock', mock_adapter)

    url = '%s/devices' % config.forest_url
    response_list = [
        # access token from ./data/user_auth_token_valid.json.
        {'status_code': 401, 'json': {'error': 'user_unauthorized'}, 'headers': {'X-QMI-AUTH-TOKEN': 'ok'}},
        # access token from new_user_auth_token.
        {'status_code': 200, 'json': [{'id': 0}], 'headers': {'X-QMI-AUTH-TOKEN': 'ok'}},
    ]
    mock_adapter.register_uri('GET', url, response_list=response_list)

    refresh_url = '%s/auth/qmi/refresh' % config.forest_url

    def refresh_matcher(request):
        body = request.json()
        return (body['refresh_token'] == 'supersecret') and (body['access_token'] == 'ok')
    new_user_auth_token = {'access_token': 'secret2', 'refresh_token': 'supersecret2'}
    mock_adapter.register_uri('POST', refresh_url, status_code=200, json=new_user_auth_token, additional_matcher=refresh_matcher)

    # refresh will write the new auth tokens to file. Do not over-write text fixture data.
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'qmi_auth_token_path', '/tmp/qmi_auth_token_invalid.json')
    config.configparsers['QCS_CONFIG'].set('Rigetti Forest', 'user_auth_token_path', '/tmp/user_auth_token_valid.json')
    devices = session.get(url).json()
    assert len(devices) == 1
    assert devices[0]['id'] == 0
