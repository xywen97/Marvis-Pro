#!/usr/bin/env python3
"""Prepare the development native service; shipped App/TUI need no setup."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
from llm_client import LLMClient, load_env_file


def configure(root=ROOT):
    config = yaml.safe_load((root / 'config.yaml').read_text()) or {}
    env_path = root / '.env'
    env = load_env_file(env_path)
    options = config.get('llm') or {}
    model = LLMClient(**{k: v for k, v in options.items() if k in ('base_url', 'model', 'api_key', 'temperature')}, env_file=str(env_path))
    values = {
        'OPENVIKING_URL': 'http://127.0.0.1:1933',
        'OPENVIKING_API_KEY': secrets.token_urlsafe(32),
        'OPENVIKING_VLM_BASE_URL': model.base_url,
        'OPENVIKING_VLM_MODEL': model.model,
        'OPENVIKING_VLM_API_KEY': model.api_key,
    }
    effective = {key: env.get(key) or value for key, value in values.items()}
    if not all(effective.values()):
        raise ValueError('请先在 .env 中配置 Marvis 模型，或填写 OPENVIKING_VLM_*；记忆提取需要可用模型。')
    # A host loopback endpoint cannot be reached as localhost inside Docker.
    effective['OPENVIKING_VLM_BASE_URL'] = effective['OPENVIKING_VLM_BASE_URL'].replace('://127.0.0.1:', '://host.docker.internal:').replace('://localhost:', '://host.docker.internal:')
    for value in effective.values():
        if any(c in value for c in '\n\r\x00\''):
            raise ValueError('OpenViking 环境变量含不支持的字符，请手动配置。')
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    keys = set(effective)
    lines = [line for line in lines if line.partition('=')[0].strip() not in keys]
    lines += ['', '# OpenViking local memory service (generated; keep credentials private)']
    lines += [f"{key}='{value}'" for key, value in effective.items()]
    env_path.write_text('\n'.join(lines) + '\n')
    os.chmod(env_path, 0o600)
    (root / '.marvis' / 'openviking').mkdir(parents=True, exist_ok=True)
    print('OpenViking 配置已写入 .env；保留已有 OpenViking 密钥和模型设置。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true', help='准备后启动本机 Marvis 后台')
    parser.add_argument('--docker', action='store_true', help='改用可选的独立 Docker 部署')
    args = parser.parse_args()
    if args.docker:
        configure()
        if args.start:
            subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compose.openviking.yaml'), 'up', '-d'], cwd=ROOT, check=True)
        print('独立 Docker 服务需要 memory.deployment: external。')
        return
    subprocess.run(['uv', 'venv', '--allow-existing', str(ROOT / '.venv-openviking'), '--python', sys.executable], check=True)
    subprocess.run(['uv', 'pip', 'install', '--python', str(ROOT / '.venv-openviking/bin/python'), '-r', str(ROOT / 'requirements-memory.txt')], check=True)
    from scripts.build_native_app import model_asset, MODEL_NAME
    model_asset(ROOT / '.cache' / 'openviking' / 'models' / MODEL_NAME)
    print('原生 OpenViking 与嵌入模型已准备好；打开 App/TUI 时自动启动。')
    if args.start:
        subprocess.run([sys.executable, str(ROOT / 'main.py'), '--serve'], cwd=ROOT, check=True)


if __name__ == '__main__': main()
