#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate-jenkinsfile.py — 从模板生成定制化 Jenkinsfile

支持下列模板类型:
  frontend        前端 SPA (npm)        → templates/Jenkinsfile
  frontend-yarn   前端 SPA (yarn v1)    → templates/frontend-yarn-Jenkinsfile
  frontend-docker 前端 SPA + Docker     → templates/frontend-docker-Jenkinsfile
  frontend-deploy 前端 SPA + SSH 部署   → templates/frontend-deploy-Jenkinsfile
  java            Java 微服务 (Maven)   → templates/java-microservice-Jenkinsfile
  python          Python 服务 (pip)     → templates/python-service-Jenkinsfile
  rust            Rust 微服务 (Cargo)   → templates/rust-microservice-Jenkinsfile
  rust-ws         Rust 微服务 + WS      → templates/rust-ws-Jenkinsfile
  go-ws           Go 服务 + WebSocket   → templates/go-ws-Jenkinsfile

配置来源优先级（高 → 低）: 命令行参数 > YAML 配置文件 (-y) > 内置默认值

用法:
  generate-jenkinsfile.py -t rust -o ./Jenkinsfile
  generate-jenkinsfile.py -y jenkins-config-rust.yml
  generate-jenkinsfile.py -y jenkins-config-rust.yml --app-name feedback-service
  generate-jenkinsfile.py --list

提交门禁（重生成比对，不落盘）:
  generate-jenkinsfile.py -y jenkins-config.yml -o Jenkinsfile --check   # 不一致 exit 1 + 漂移位置/diff/修复命令
  generate-jenkinsfile.py -y jenkins-config.yml -o Jenkinsfile --diff    # 仅展示差异（漂移也 exit 0）
"""

import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "templates"))

DEFAULTS = {
    "output": "./Jenkinsfile",
    "env": "dev",
    # 通用
    "git_credential_id": "chaineasy",
    # frontend*
    "node_version": "24",
    "npm_registry": "",
    "node_options": "",
    # 构建类型/包管理器: npm | yarn | pnpm | bun (frontend-ssr; 默认 npm, 向后兼容)
    "package_manager": "npm",
    # 容器环境变量来源: 空=用 APP_ENV_VARS 参数; 填 credentialId=从 Secret text 凭据读取
    #   （凭据方式: Jenkins 自动掩码、不落入构建参数记录、AUTH_SECRET 跨次部署保持稳定）
    "app_env_credential_id": "",
    # 质量门禁命令 (npm test / npm run typecheck 等) 与产物输出目录
    "test_command": "npm test",
    "test_stage_name": "Unit Test",
    "test_enabled": True,
    "build_output_dir": "dist",
    # java
    "maven_home": "/opt/apache-maven-3.9.6",
    "java_home": "/opt/jdk-17",
    # python
    "python_version": "3.11",
    "pip_index": "https://pypi.tuna.tsinghua.edu.cn/simple",
    # docker/deploy
    "image_name": "my-app",
    "docker_registry": "registry.example.com",
    "docker_registry_cache": "registry.example.com",
    "docker_credentials_id": "docker-hub",
    "ssh_user": "root",
    "app_port": "3000",
    "app_env_vars": "NODE_ENV=production",
    "deploy_host": "server.example.com",
    "deploy_port": "22",
    "deploy_user": "deployuser",
    "deploy_owner": "deployuser",
    "deploy_domain": "www.example.com",
    "deploy_credential_id": "deployuser-aliyun-106.15.63.142",
    "deploy_base": "/opt/www",
    # rust
    "rust_version": "1.90.0",
    "app_name": "my-rust-service",
    "app_port": "8084",
    "expose_app_port": "8084",
    "dev_server_ip": "192.168.1.12",
    "prod_server_ip": "192.168.1.102",
    "ssh_user": "root",
    "nacos_serveraddr": "192.168.1.11:8848",
    "nacos_namespace": "",
    "nacos_dataid": "my-rust-service",
    "nacos_group": "DEFAULT_GROUP",
    "nacos_username": "base",
    "nacos_password": "",  # 无内置口令；由各仓 jenkins-config.yml 提供（缺失=空，勿内置默认）
    "deploy_ssh_cred_id": "deploy-server-ssh-key",
    "ws_port": "",
    "health_path": "/health",
    # G5 部署后自证：可选 e2e 冒烟命令 与 /build.json 校验（默认关）
    "e2e_cmd": "",
    # Merge Back 目标分支（各仓 trunk 可能 main/master）
    "merge_branch": "master",
    # 发布分支固定值（留空=自动取最新 release/*）
    "release_branch": "",
    "build_json": False,
    # go
    "go_version": "1.24",
}

TYPES = {
    "frontend": ("Jenkinsfile", "前端 SPA (npm)"),
    "frontend-yarn": ("frontend-yarn-Jenkinsfile", "前端 SPA (yarn v1)"),
    "frontend-docker": ("frontend-docker-Jenkinsfile", "前端 SPA + Docker 镜像推送"),
    "frontend-deploy": ("frontend-deploy-Jenkinsfile", "前端 SPA + SSH 部署"),
    "frontend-ssr": ("frontend-ssr-Jenkinsfile", "Next.js SSR 前后端不分离 (Docker + SSH)"),
    "java": ("java-microservice-Jenkinsfile", "Java 微服务 (Maven)"),
    "python": ("python-service-Jenkinsfile", "Python 服务 (pip + pytest)"),
    "rust": ("rust-microservice-Jenkinsfile", "Rust 微服务 (Cargo + Docker + SSH 部署)"),
    "rust-ws": ("rust-ws-Jenkinsfile", "Rust 微服务 + WebSocket (HTTP + WS 双端口)"),
    "go-ws": ("go-ws-Jenkinsfile", "Go 服务 + WebSocket (HTTP + WS 双端口, CGO)"),
}


def g5_extra(cfg: dict) -> str:
    """G5 selfcheck 的可选追加参数：--build-json / --e2e-cmd '<cmd>'（未配则为空串）。"""
    args = []
    if cfg.get("build_json"):
        args.append("--build-json")
    e2e = (cfg.get("e2e_cmd") or "").strip()
    if e2e:
        args.append("--e2e-cmd '" + e2e.replace("'", "'\\''") + "'")
    return (" " + " ".join(args)) if args else ""


def build_choices(env: str, choices: str) -> str:
    """构造 choice 参数: 将选中环境放首位 (成为默认选项)"""
    items = [env] + [e for e in choices.split() if e != env]
    return "choices: [" + ", ".join(f"'{e}'" for e in items) + "]"


def replace(content: str, old: str, new: str) -> str:
    return content.replace(old, new)


def sed_escape(value: str) -> str:
    return value.replace("&", "\\&").replace("|", "\\|")


def groovy_single_quote(value: str) -> str:
    """把多行文本转成 **单引号 Groovy 字符串字面量** 的安全内容。

    Groovy 的单引号字符串**不能跨行**（'a\nb' 非法，需三引号），所以换行必须
    转义成 \\n，否则生成物在 Jenkins 解析阶段直接失败：
        expecting ''', found '\n'  (WorkflowScript: NN)
    （参见 APP_ENV_VARS 多行默认值事故）
    """
    s = (value or "").replace("\r\n", "\n").rstrip("\n")
    s = s.replace("\\", "\\\\")      # 先转义反斜杠，避免误伤后续新增的 \n
    s = s.replace("'", "\\'")        # 单引号需转义
    s = s.replace("\n", "\\n")        # 换行 → 字面量 \n（Groovy 运行时解析为换行）
    return s


def render(template_name: str, replacements: dict, extra: dict = None) -> str:
    path = os.path.join(TEMPLATES_DIR, template_name)
    if not os.path.isfile(path):
        print(f"❌ 错误: 模板不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    for old, new in (replacements or {}).items():
        content = content.replace(old, new)
    return content


def gen_frontend(cfg, template: str) -> str:
    print(f"🔧 环境: {cfg['env']}, Node: {cfg['node_version']}, 凭证: {cfg['git_credential_id']}")
    reps = {
        "defaultValue: '24'": f"defaultValue: '{cfg['node_version']}'",
        "defaultValue: '18'": f"defaultValue: '{cfg['node_version']}'",
        "defaultValue: 'yarn-project'": f"defaultValue: '{cfg['git_credential_id']}'",
        "defaultValue: 'chaineasy'": f"defaultValue: '{cfg['git_credential_id']}'",
        "choices: ['dev', 'prod', 'test']": build_choices(cfg["env"], "dev prod test"),
        "{{TEST_STAGE_NAME}}": cfg["test_stage_name"],
        "{{TEST_COMMAND}}": cfg["test_command"],
        "{{BUILD_OUTPUT_DIR}}": cfg["build_output_dir"],
    }
    if cfg["npm_registry"]:
        reps["https://registry.npmmirror.com"] = cfg["npm_registry"]
    if cfg["node_options"]:
        reps['NODE_OPTIONS = "--max-old-space-size=4096"'] = f'NODE_OPTIONS = "{cfg["node_options"]}"'
    content = render(template, reps)
    return strip_test_stage(content, cfg)


def strip_test_stage(content: str, cfg: dict) -> str:
    """质量门禁阶段开关 (模板中 {{TEST_STAGE_BLOCK}} 标记之间)。

    - test_enabled=true  → 保留门禁阶段，同时剥离标记行（漏标记会让 {{...}} 留在 stages 内，
      Declarative Pipeline 直接解析失败）
    - test_enabled=false → 连标记一起移除整个阶段块
    """
    start_marker = "{{TEST_STAGE_BLOCK}}"
    end_marker = "{{\u002fTEST_STAGE_BLOCK}}"
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start == -1 or end == -1:
        return content
    if cfg.get("test_enabled", True):
        return content.replace(start_marker, "").replace(end_marker, "")
    return content[:start] + content[end + len(end_marker):]


def gen_frontend_docker(cfg) -> str:
    print(f"🔧 环境: {cfg['env']}, Node: {cfg['node_version']}, 镜像: {cfg['docker_registry']}/{cfg['image_name']}")
    reps = {
        "defaultValue: '22'": f"defaultValue: '{cfg['node_version']}'",
        "defaultValue: 'my-spa'": f"defaultValue: '{cfg['image_name']}'",
        "defaultValue: 'my-spa-git'": f"defaultValue: '{cfg['git_credential_id']}'",
        "defaultValue: 'registry.example.com'": f"defaultValue: '{cfg['docker_registry']}'",
        "defaultValue: 'docker-hub'": f"defaultValue: '{cfg['docker_credentials_id']}'",
        "choices: ['dev', 'prod', 'test']": build_choices(cfg["env"], "dev prod test"),
        "{{TEST_STAGE_NAME}}": cfg["test_stage_name"],
        "{{TEST_COMMAND}}": cfg["test_command"],
        "{{BUILD_OUTPUT_DIR}}": cfg["build_output_dir"],
    }
    if cfg["npm_registry"]:
        reps["https://registry.npmmirror.com"] = cfg["npm_registry"]
    if cfg["node_options"]:
        reps['NODE_OPTIONS = "--max-old-space-size=4096"'] = f'NODE_OPTIONS = "{cfg["node_options"]}"'
    return strip_test_stage(render("frontend-docker-Jenkinsfile", reps), cfg)


def pm_commands(pm: str, registry: str) -> dict:
    """按构建类型(包管理器)给出 版本命令 / 安装命令 / 脚本运行前缀 / 环境准备块。

    - npm   : 保持旧模板行为 (npm install --registry=淘宝源)
    - bun   : bun install --frozen-lockfile (消费入库的 bun.lock) + $HOME/.bun 免 root 自装
    - yarn  : yarn v1 --frozen-lockfile
    - pnpm  : pnpm --frozen-lockfile
    注: bun/yarn/pnpm 的 PATH 引导块写在 Groovy 三引号串内, 故 \\$ 转义为 shell 的 $。
    """
    pm = (pm or "npm").strip().lower()
    reg = registry or "https://registry.npmmirror.com"
    if pm == "bun":
        install = "bun install --frozen-lockfile"
        if registry:
            install += f" --registry={registry}"
        setup = (
            '                        # bun 不由 nvm 管理: 未安装时自动装到 \\$HOME/.bun（免 root）\n'
            '                        export PATH="\\$HOME/.bun/bin:\\$PATH"\n'
            '                        if ! command -v bun >/dev/null 2>&1; then\n'
            '                            echo "--- 安装 bun (官方脚本, 免 root) ---"\n'
            '                            curl -fsSL https://bun.sh/install | bash || { echo "❌ bun 安装失败: 请先安装 bun 或在 jenkins-config.yml 改回 package_manager: npm"; exit 1; }\n'
            '                            export PATH="\\$HOME/.bun/bin:\\$PATH"\n'
            '                        fi'
        )
        # 每个 sh 阶段都是新 shell：nvm 阶段后需重新引入 bun 的 PATH（agent 自行已装 bun 时该行无害）
        path = (
            '                        export PATH="\\$HOME/.bun/bin:\\$PATH"\n'
            '                        command -v bun >/dev/null 2>&1 || { echo "❌ bun 未找到: Install 阶段应已装到 \\$HOME/.bun（或 agent 已全局安装）"; exit 1; }'
        )
        return {"name": "bun", "install": install, "run": "bun run", "setup": setup, "path": path}
    if pm == "yarn":
        install = "yarn install --frozen-lockfile"
        if registry:
            install += f" --registry={registry}"
        return {"name": "yarn", "install": install, "run": "yarn", "setup": "", "path": ""}
    if pm == "pnpm":
        install = "pnpm install --frozen-lockfile"
        if registry:
            install += f" --registry={registry}"
        return {"name": "pnpm", "install": install, "run": "pnpm run", "setup": "", "path": ""}
    return {"name": "npm", "install": f"npm install --registry={reg}", "run": "npm run", "setup": "", "path": ""}


def gen_frontend_ssr(cfg) -> str:
    print(f"🔧 环境: {cfg['env']}, Node: {cfg['node_version']}, 构建类型: {cfg['package_manager']}, "
          f"镜像: {cfg['docker_registry']}/{cfg['image_name']}, "
          f"部署: {cfg['ssh_user']}@{cfg['deploy_host']}:{cfg['expose_app_port']}")
    # DEFAULTS 中 expose_app_port=8084 是 rust 哨兵默认, SSR 兜底 3000 (YAML 可覆盖)
    expose = cfg["expose_app_port"] if cfg.get("expose_app_port") != "8084" else "3000"
    reps = {
        "defaultValue: '22'": f"defaultValue: '{cfg['node_version']}'",
        "defaultValue: 'my-spa-git'": f"defaultValue: '{cfg['git_credential_id']}'",
        "defaultValue: 'my-ssr-app'": f"defaultValue: '{cfg['image_name']}'",
        "defaultValue: 'registry.example.com'": f"defaultValue: '{cfg['docker_registry']}'",
        "defaultValue: 'docker-hub'": f"defaultValue: '{cfg['docker_credentials_id']}'",
        "defaultValue: 'server.example.com'": f"defaultValue: '{cfg['deploy_host']}'",
        "defaultValue: '22'": f"defaultValue: '{cfg['deploy_port']}'",
        "defaultValue: 'root'": f"defaultValue: '{cfg['ssh_user']}'",
        "defaultValue: 'deploy-key'": f"defaultValue: '{cfg['deploy_credential_id']}'",
        "string(name: 'APP_PORT', defaultValue: '3000'": f"string(name: 'APP_PORT', defaultValue: '{cfg['app_port']}'",
        "string(name: 'EXPOSE_APP_PORT', defaultValue: '3000'": f"string(name: 'EXPOSE_APP_PORT', defaultValue: '{expose}'",
        "choices: ['dev', 'prod', 'test']": build_choices(cfg["env"], "dev prod test"),
        "{{TEST_STAGE_NAME}}": cfg["test_stage_name"],
        "{{TEST_COMMAND}}": cfg["test_command"],
        "{{BUILD_OUTPUT_DIR}}": cfg["build_output_dir"],
        "NODE_ENV=production": groovy_single_quote(cfg["app_env_vars"]),
        "@@G5_EXTRA@@": g5_extra(cfg),
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
        "string(name: 'HEALTH_PATH', defaultValue: '/health'": f"string(name: 'HEALTH_PATH', defaultValue: '{cfg.get('health_path') or '/health'}'",
    }
    # 包管理器(构建类型): npm/yarn/pnpm/bun — 模板占位符统一替换
    pm = pm_commands(cfg["package_manager"], cfg["npm_registry"])
    reps["{{PKG_NAME}}"] = pm["name"]
    reps["{{PKG_INSTALL}}"] = pm["install"]
    reps["{{PKG_RUN}}"] = pm["run"]
    reps["{{PKG_SETUP}}"] = pm["setup"]
    reps["{{PKG_PATH}}"] = pm["path"]
    # 容器环境变量来源: 参数(默认) 或 Secret text 凭据(凭据方式自动掩码，见 DEFAULTS 注释)
    cred = (cfg.get("app_env_credential_id") or "").strip()
    if cred:
        reps["{{APP_ENV_CRED_OPEN}}"] = (
            f"                    withCredentials([string(credentialsId: '{cred}', "
            "variable: 'APP_ENV_VARS_CRED')]) {"
        )
        reps["{{APP_ENV_CRED_CLOSE}}"] = "                    }"
        reps["{{APP_ENV_EXPR}}"] = "env.APP_ENV_VARS_CRED"
    else:
        reps["{{APP_ENV_CRED_OPEN}}"] = ""
        reps["{{APP_ENV_CRED_CLOSE}}"] = ""
        reps["{{APP_ENV_EXPR}}"] = "params.APP_ENV_VARS"
    if cfg["node_options"]:
        reps['NODE_OPTIONS = "--max-old-space-size=4096"'] = f'NODE_OPTIONS = "{cfg["node_options"]}"'
    return strip_test_stage(render("frontend-ssr-Jenkinsfile", reps), cfg)


def gen_frontend_deploy(cfg) -> str:
    base = cfg.get("deploy_base", "/opt/www")
    print(f"🔧 环境: {cfg['env']}, 部署: {cfg['deploy_user']}@{cfg['deploy_host']} → {base}/releases/{cfg['deploy_domain']}/<build> + current")
    reps = {
        "defaultValue: '24'": f"defaultValue: '{cfg['node_version']}'",
        "defaultValue: 'my-spa-git'": f"defaultValue: '{cfg['git_credential_id']}'",
        "defaultValue: 'server.example.com'": f"defaultValue: '{cfg['deploy_host']}'",
        "defaultValue: '22'": f"defaultValue: '{cfg['deploy_port']}'",
        "string(name: 'DEPLOY_USER', defaultValue: 'deployuser'": f"string(name: 'DEPLOY_USER', defaultValue: '{cfg['deploy_user']}'",
        "defaultValue: '/opt/www'": f"defaultValue: '{base}'",
        "defaultValue: 'www.example.com'": f"defaultValue: '{cfg['deploy_domain']}'",
        "defaultValue: 'deployuser-aliyun-106.15.63.142'": f"defaultValue: '{cfg['deploy_credential_id']}'",
        "choices: ['dev', 'prod', 'test']": build_choices(cfg["env"], "dev prod test"),
        "{{TEST_STAGE_NAME}}": cfg["test_stage_name"],
        "{{TEST_COMMAND}}": cfg["test_command"],
        "{{BUILD_OUTPUT_DIR}}": cfg["build_output_dir"],
        "@@G5_EXTRA@@": g5_extra(cfg),
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
        "string(name: 'HEALTH_PATH', defaultValue: '/'": f"string(name: 'HEALTH_PATH', defaultValue: '{cfg.get('health_path') or '/'}'",
    }
    if cfg["npm_registry"]:
        reps["https://registry.npmmirror.com"] = cfg["npm_registry"]
    if cfg["node_options"]:
        reps['NODE_OPTIONS = "--max-old-space-size=4096"'] = f'NODE_OPTIONS = "{cfg["node_options"]}"'
    return strip_test_stage(render("frontend-deploy-Jenkinsfile", reps), cfg)


def gen_java(cfg) -> str:
    print(f"🔧 环境: {cfg['env']}, Maven: {cfg['maven_home']}, JDK: {cfg['java_home']}")
    content = render("java-microservice-Jenkinsfile", {})
    content = content.replace("/opt/apache-maven-3.9.6", cfg["maven_home"])
    content = content.replace("/opt/jdk-17", cfg["java_home"])
    content = content.replace("choices: ['dev', 'test', 'prod']", build_choices(cfg["env"], "dev test prod"))
    return content


def gen_python(cfg) -> str:
    print(f"🔧 Python: {cfg['python_version']}, pip: {cfg['pip_index']}, 凭证: {cfg['git_credential_id']}")
    reps = {
        "defaultValue: '3.11'": f"defaultValue: '{cfg['python_version']}'",
        "defaultValue: 'python-service'": f"defaultValue: '{cfg['git_credential_id']}'",
        "https://pypi.tuna.tsinghua.edu.cn/simple": cfg["pip_index"],
    }
    return render("python-service-Jenkinsfile", reps)


def gen_rust(cfg, template_name="rust-microservice-Jenkinsfile") -> str:
    print(f"🔧 环境: {cfg['env']}, Rust: {cfg['rust_version']}, 应用: {cfg['app_name']}, "
          f"部署: {cfg['ssh_user']}@{cfg['dev_server_ip']}:{cfg['expose_app_port']}")
    reps = {
        "defaultValue: '1.90.0'": f"defaultValue: '{cfg['rust_version']}'",
        "defaultValue: 'my-rust-service'": f"defaultValue: '{cfg['app_name']}'",
        "defaultValue: '192.168.1.12'": f"defaultValue: '{cfg['dev_server_ip']}'",
        "defaultValue: '192.168.1.102'": f"defaultValue: '{cfg['prod_server_ip']}'",
        "defaultValue: 'root'": f"defaultValue: '{cfg['ssh_user']}'",
        "defaultValue: '8084'": f"defaultValue: '{cfg['expose_app_port']}'",
        "defaultValue: '192.168.1.7:5000'": f"defaultValue: '{cfg['docker_registry']}'",
        "defaultValue: '192.168.1.7:5001'": f"defaultValue: '{cfg['docker_registry_cache']}'",
        "defaultValue: '192.168.1.11:8848'": f"defaultValue: '{cfg['nacos_serveraddr']}'",
        "defaultValue: 'DEFAULT_GROUP'": f"defaultValue: '{cfg['nacos_group']}'",
        "defaultValue: 'base'": f"defaultValue: '{cfg['nacos_username']}'",
        "defaultValue: '@@NACOS_PASSWORD@@'": f"defaultValue: '{cfg['nacos_password']}'",
        "defaultValue: 'chaineasy'": f"defaultValue: '{cfg['git_credential_id']}'",
        "defaultValue: 'docker-registry-auth'": f"defaultValue: '{cfg['docker_credentials_id']}'",
        "defaultValue: 'deploy-server-ssh-key'": f"defaultValue: '{cfg['deploy_ssh_cred_id']}'",
        "choices: ['dev', 'prod', 'test']": build_choices(cfg["env"], "dev prod test"),
        "defaultValue: '/health'": f"defaultValue: '{cfg.get('health_path') or '/health'}'",
        "@@G5_EXTRA@@": g5_extra(cfg),
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
    }
    content = render(template_name, reps)
    # NACOS_DATAID / NACOS_NAMESPACE 独立替换 (模板占位与 APP_NAME 相同/为空, 需精确到行, 避免被全局替换误伤)
    content = re.sub(r"string\(name: 'NACOS_DATAID', defaultValue: '[^']*'",
                     f"string(name: 'NACOS_DATAID', defaultValue: '{cfg['nacos_dataid']}'", content)
    content = re.sub(r"string\(name: 'NACOS_NAMESPACE', defaultValue: '[^']*'",
                     f"string(name: 'NACOS_NAMESPACE', defaultValue: '{cfg['nacos_namespace']}'", content)
    return content


def gen_rust_ws(cfg) -> str:
    """Rust + WebSocket: 基于 rust-ws 模板, 注入 WS 端口映射"""
    content = gen_rust(cfg, template_name="rust-ws-Jenkinsfile")
    ws = cfg.get("ws_port", "")
    if ws:
        content = content.replace("'@@WS_PORT@@'", f"'{ws}'")
    else:
        # 未配置 WS 端口: 删除 WS 参数行与映射行（按行过滤，避免转义不匹配导致映射行残留 ✗ 2026-09-26 实测）
        content = "".join(ln for ln in content.splitlines(keepends=True) if "WS_PORT" not in ln)
    return content


def gen_go_ws(cfg) -> str:
    """Go + WebSocket: 基于 go-ws 模板, 注入 Go/端口/WS 映射"""
    print(f"\U0001f527 Go {cfg['go_version']}, 应用: {cfg['app_name']}, "
          f"部署: {cfg['ssh_user']}@{cfg['dev_server_ip']}:{cfg['expose_app_port']}, WS: {cfg.get('ws_port') or '-'}")
    reps = {
        "defaultValue: '192.168.1.12'": f"defaultValue: '{cfg['dev_server_ip']}'",
        "defaultValue: '192.168.1.102'": f"defaultValue: '{cfg['prod_server_ip']}'",
        "defaultValue: 'root'": f"defaultValue: '{cfg['ssh_user']}'",
        "defaultValue: 'my-go-service'": f"defaultValue: '{cfg['app_name']}'",
        "defaultValue: '192.168.1.7:5000'": f"defaultValue: '{cfg['docker_registry']}'",
        "defaultValue: '192.168.1.7:5001'": f"defaultValue: '{cfg['docker_registry_cache']}'",
        "defaultValue: '1.24'": f"defaultValue: '{cfg['go_version']}'",
        "defaultValue: '192.168.1.11:8848'": f"defaultValue: '{cfg['nacos_serveraddr']}'",
        "defaultValue: 'DEFAULT_GROUP'": f"defaultValue: '{cfg['nacos_group']}'",
        "defaultValue: 'base'": f"defaultValue: '{cfg['nacos_username']}'",
        "defaultValue: '@@NACOS_PASSWORD@@'": f"defaultValue: '{cfg['nacos_password']}'",
        "defaultValue: 'chaineasy'": f"defaultValue: '{cfg['git_credential_id']}'",
        "defaultValue: 'docker-registry-auth'": f"defaultValue: '{cfg['docker_credentials_id']}'",
        "defaultValue: 'deploy-server-ssh-key'": f"defaultValue: '{cfg['deploy_ssh_cred_id']}'",
        "choices: ['dev', 'prod', 'test']": build_choices(cfg["env"], "dev prod test"),
        "@@G5_EXTRA@@": g5_extra(cfg),
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
        "@@MERGE_BRANCH@@": cfg["merge_branch"],
        "@@RELEASE_BRANCH@@": cfg["release_branch"],
    }
    content = render("go-ws-Jenkinsfile", reps)
    # 精确到行替换 (模板默认值可能与 app_name 相同/为空, 避免全局替换误伤)
    content = re.sub(r"string\(name: 'EXPOSE_APP_PORT', defaultValue: '[^']*'",
                     f"string(name: 'EXPOSE_APP_PORT', defaultValue: '{cfg['expose_app_port']}'", content)
    content = re.sub(r"string\(name: 'APP_PORT', defaultValue: '[^']*'",
                     f"string(name: 'APP_PORT', defaultValue: '{cfg['app_port']}'", content)
    content = re.sub(r"string\(name: 'HEALTH_PATH', defaultValue: '[^']*'",
                     f"string(name: 'HEALTH_PATH', defaultValue: '{cfg.get('health_path') or '/health'}'", content)
    content = re.sub(r"string\(name: 'NACOS_DATAID', defaultValue: '[^']*'",
                     f"string(name: 'NACOS_DATAID', defaultValue: '{cfg['nacos_dataid']}'", content)
    content = re.sub(r"string\(name: 'NACOS_NAMESPACE', defaultValue: '[^']*'",
                     f"string(name: 'NACOS_NAMESPACE', defaultValue: '{cfg['nacos_namespace']}'", content)
    ws = cfg.get("ws_port", "")
    if ws:
        content = content.replace("'@@WS_PORT@@'", f"'{ws}'")
    else:
        # 未配置 WS 端口: 删除 WS 参数行与映射行 (按行过滤)
        content = "".join(ln for ln in content.splitlines(keepends=True) if "WS_PORT" not in ln)
    return content


GENERATORS = {
    "frontend": lambda cfg: gen_frontend(cfg, "Jenkinsfile"),
    "frontend-yarn": lambda cfg: gen_frontend(cfg, "frontend-yarn-Jenkinsfile"),
    "frontend-docker": gen_frontend_docker,
    "frontend-deploy": gen_frontend_deploy,
    "frontend-ssr": gen_frontend_ssr,
    "java": gen_java,
    "python": gen_python,
    "rust": gen_rust,
    "rust-ws": gen_rust_ws,
    "go-ws": gen_go_ws,
}


def load_yaml(path: str) -> dict:
    if yaml is None:
        print("❌ 错误: 需要 pyyaml (pip install pyyaml)", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        print("❌ 错误: YAML 顶层必须是 key: value 映射", file=sys.stderr)
        sys.exit(1)
    return {k: v for k, v in data.items() if v is not None}


def render_ssr_dockerfile(cfg: dict) -> str:
    """渲染 frontend-ssr 的 Next.js standalone Dockerfile 内容 (不落盘)。"""
    with open(os.path.join(TEMPLATES_DIR, "nextjs-standalone.Dockerfile"), encoding="utf-8") as f:
        dcontent = f.read()
    return dcontent.replace("FROM node:22-alpine", f"FROM node:{cfg['node_version']}-alpine")


def _drift_lines(actual: str, expected: str) -> str:
    """用 difflib 给出漂移所在的**行号区间**，便于一眼定位被手改/过期位置。"""
    import difflib
    sm = difflib.SequenceMatcher(None, actual.splitlines(), expected.splitlines())
    parts = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:
            parts.append(f"期望新增第 {j1 + 1}-{j2} 行")
        elif j1 == j2:
            parts.append(f"仓库多出/被改第 {i1 + 1}-{i2} 行")
        else:
            parts.append(f"仓库第 {i1 + 1}-{i2} 行 ↔ 重生成第 {j1 + 1}-{j2} 行")
    if not parts:
        return "(无行级差异，可能仅行尾/编码不同)"
    shown = "; ".join(parts[:8])
    return shown + (f" …(共 {len(parts)} 处)" if len(parts) > 8 else "")


def build_fix_command(cfg: dict, output: str) -> str:
    """生成可用于一键修复的重新生成命令。"""
    gen = os.path.abspath(__file__)
    if cfg.get("_yaml"):
        return f"python3 {gen} -y {cfg['_yaml']} -o {output}"
    return f"python3 {gen} -t {cfg['type']} -o {output}"


def do_check(output: str, expected: str, cfg: dict, show_diff_only: bool) -> int:
    """提交门禁核心: 由配置重生成 → 与目标文件比对 → 不一致报异常。

    异常分类:
      - MISSING  : 目标 Jenkinsfile 缺失 (生成物未提交)
      - DRIFT    : 生成块被手改，或模板/配置已更新但生成物未重生成
    (占位未替换在生成阶段已拦下，退出 1)
    输出含 **漂移位置 + 实际/期望 diff + 修复命令**。
    """
    import difflib
    sys.stdout.flush()
    fix = build_fix_command(cfg, output)
    if not os.path.exists(output):
        print(f"❌ Jenkinsfile 门禁 FAIL: 目标文件缺失 → {output}", file=sys.stderr)
        print("   异常分类: MISSING（生成物未提交）", file=sys.stderr)
        print(f"   修复命令: {fix}", file=sys.stderr)
        return 0 if show_diff_only else 1

    with open(output, encoding="utf-8") as f:
        actual = f.read()
    if actual == expected:
        print(f"✅ Jenkinsfile 门禁 PASS: {output} 与配置重生成结果一致")
        return 0

    diff = list(difflib.unified_diff(
        actual.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"a/{output} (仓库内·实际)",
        tofile=f"b/{output} (由配置重生成·期望)",
    ))
    print(f"❌ Jenkinsfile 门禁 FAIL: {output} 漂移（生成块被手改或未重生成）", file=sys.stderr)
    print("   异常分类: DRIFT", file=sys.stderr)
    print(f"   漂移位置: {_drift_lines(actual, expected)}", file=sys.stderr)
    print("   --- 实际(仓库) → 期望(重生成) ---", file=sys.stderr)
    sys.stderr.writelines(diff)
    if diff and not diff[-1].endswith("\n"):
        print(file=sys.stderr)
    print(f"   修复命令: {fix}", file=sys.stderr)
    print("   说明: Jenkinsfile 为生成物，禁止直接手改；请改 jenkins-config.yml/模板后重新生成",
          file=sys.stderr)
    return 0 if show_diff_only else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="generate-jenkinsfile.py",
        description="从模板生成定制化 Jenkinsfile (支持 7 种类型, 优先级: CLI > YAML > 默认值)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-t", "--type", choices=sorted(TYPES), help="项目类型")
    parser.add_argument("-o", "--output", help="输出路径 (默认 ./Jenkinsfile)")
    parser.add_argument("-e", "--env", choices=["dev", "test", "prod"], help="目标环境 (默认 dev)")
    parser.add_argument("-y", "--yaml", help="YAML 配置驱动 (如 jenkins-config-rust.yml)")
    parser.add_argument("-l", "--list", action="store_true", help="列出全部模板类型")
    parser.add_argument("--check", action="store_true",
                        help="仅校验: 由配置重生成并与目标 Jenkinsfile 比对, 不一致 exit 1 (提交门禁用, 不落盘)")
    parser.add_argument("--diff", action="store_true",
                        help="仅展示仓库文件与重生成结果的差异 (不落盘), 漂移也 exit 0 (除非配置错误)")
    # frontend
    parser.add_argument("-n", "--node-version", help="Node 版本")
    parser.add_argument("--npm-registry", help="npm 镜像源")
    parser.add_argument("--node-options", help="NODE_OPTIONS")
    parser.add_argument("--package-manager", choices=["npm", "yarn", "pnpm", "bun"],
                        help="构建类型/包管理器 (frontend-ssr; 默认 npm)")
    parser.add_argument("--app-env-credential-id",
                        help="容器环境变量取自该 Secret text 凭据（不填则用 APP_ENV_VARS 参数）")
    # java
    parser.add_argument("-m", "--maven-home", help="Maven 路径")
    parser.add_argument("-j", "--java-home", help="JDK 路径")
    # python
    parser.add_argument("-p", "--python-version", help="Python 版本")
    parser.add_argument("--pip-index", help="pip 镜像源")
    # docker/deploy (frontend-docker / frontend-deploy)
    parser.add_argument("--image-name", help="镜像名")
    parser.add_argument("--docker-registry", help="Docker 私有仓库")
    parser.add_argument("--docker-registry-cache", help="Docker 缓存仓库")
    parser.add_argument("--docker-credentials-id", help="Docker 凭证 ID")
    parser.add_argument("--deploy-host", help="部署主机")
    parser.add_argument("--deploy-port", help="部署端口")
    parser.add_argument("--deploy-user", help="部署登录用户")
    parser.add_argument("--deploy-owner", help="站点属主")
    parser.add_argument("--deploy-domain", help="部署域名")
    parser.add_argument("--deploy-credential-id", help="部署凭证 ID")
    # rust
    parser.add_argument("--rust-version", help="Rust 版本")
    parser.add_argument("--go-version", help="Go 版本 (go-ws 类型)")
    parser.add_argument("--app-name", help="应用服务名称")
    parser.add_argument("--app-port", help="容器内部端口")
    parser.add_argument("--expose-app-port", help="宿主机暴露端口")
    parser.add_argument("--dev-server-ip", help="Dev 部署节点 IP")
    parser.add_argument("--prod-server-ip", help="Prod 部署节点 IP")
    parser.add_argument("--ssh-user", help="部署 SSH 用户")
    parser.add_argument("--nacos-serveraddr", help="Nacos 地址")
    parser.add_argument("--nacos-namespace", help="Nacos 命名空间")
    parser.add_argument("--nacos-dataid", help="Nacos Data ID")
    parser.add_argument("--nacos-group", help="Nacos Group")
    parser.add_argument("--nacos-username", help="Nacos 用户名")
    parser.add_argument("--nacos-password", help="Nacos 密码")
    parser.add_argument("--deploy-ssh-cred-id", help="部署 SSH 凭证 ID")
    # rust-ws
    parser.add_argument("--ws-port", help="WebSocket 端口 (rust-ws 类型, 双端口映射)")
    parser.add_argument("--e2e-cmd", help="G5 部署后可选 e2e 冒烟命令（如 'bash scripts/postdeploy-smoke.sh'）")
    parser.add_argument("--build-json", action="store_true", help="G5 校验 /build.json（buildNo/distSha256）")
    parser.add_argument("--merge-branch", help="Merge Back 目标分支（默认 master；trunk 为 main 的服务填 main）")
    parser.add_argument("--release-branch", help="固定发布分支（留空=自动取最新 release/*）")
    args = parser.parse_args()

    if args.list:
        print("可用模板类型:")
        for name in sorted(TYPES):
            print(f"  {name:<16} {TYPES[name][1]:<28} → templates/{TYPES[name][0]}")
        print("\n配置示例: templates/jenkins-config-{type}.yml")
        return 0

    # --- 合并配置: CLI > YAML > 默认 ---
    cfg = dict(DEFAULTS)
    if args.yaml:
        if not os.path.isfile(args.yaml):
            print(f"❌ 错误: 配置文件缺失 → {args.yaml}", file=sys.stderr)
            print("   异常分类: CONFIG_MISSING", file=sys.stderr)
            return 1
        print(f"📄 读取 YAML 配置: {args.yaml}")
        cfg.update({k.replace("-", "_"): v for k, v in load_yaml(args.yaml).items()})
        cfg["_yaml"] = args.yaml

    cli_map = {
        "type": args.type, "output": args.output, "env": args.env,
        "node_version": args.node_version, "maven_home": args.maven_home, "java_home": args.java_home,
        "python_version": args.python_version, "git_credential_id": None,
        "npm_registry": args.npm_registry, "node_options": args.node_options,
        "package_manager": args.package_manager,
        "app_env_credential_id": args.app_env_credential_id,
        "pip_index": args.pip_index, "image_name": args.image_name,
        "docker_registry": args.docker_registry, "docker_registry_cache": args.docker_registry_cache,
        "docker_credentials_id": args.docker_credentials_id,
        "deploy_host": args.deploy_host, "deploy_port": args.deploy_port,
        "deploy_user": args.deploy_user, "deploy_owner": args.deploy_owner,
        "deploy_domain": args.deploy_domain, "deploy_credential_id": args.deploy_credential_id,
        "rust_version": args.rust_version, "go_version": args.go_version, "app_name": args.app_name,
        "app_port": args.app_port, "expose_app_port": args.expose_app_port,
        "dev_server_ip": args.dev_server_ip, "prod_server_ip": args.prod_server_ip,
        "ssh_user": args.ssh_user,
        "nacos_serveraddr": args.nacos_serveraddr, "nacos_namespace": args.nacos_namespace,
        "nacos_dataid": args.nacos_dataid, "nacos_group": args.nacos_group,
        "nacos_username": args.nacos_username, "nacos_password": args.nacos_password,
        "deploy_ssh_cred_id": args.deploy_ssh_cred_id,
        "ws_port": args.ws_port,
        "e2e_cmd": args.e2e_cmd, "build_json": (True if args.build_json else None),
        "merge_branch": args.merge_branch,
        "release_branch": args.release_branch,
    }
    for key, val in cli_map.items():
        if val is not None:
            cfg[key] = val

    # --- 校验 ---
    if not cfg["type"]:
        print(f"❌ 错误: 必须指定项目类型 (-t { '|'.join(sorted(TYPES)) }，或 YAML 中 type: ...)", file=sys.stderr)
        print("   异常分类: CONFIG_MISSING（缺少 type）", file=sys.stderr)
        return 1
    if cfg["type"] not in GENERATORS:
        print(f"❌ 错误: 不支持的项目类型: {cfg['type']}", file=sys.stderr)
        print("   异常分类: CONFIG_MISSING（type 非法）", file=sys.stderr)
        return 1
    output = cfg["output"]
    print(f"🔧 使用模板: {TYPES[cfg['type']][0]}")
    content = GENERATORS[cfg["type"]](cfg)
    # 防呆: 残留 {{...}} 模板标记进入 Jenkinsfile 会导致 Declarative Pipeline 解析失败
    leftovers = sorted(set(re.findall(r"\{\{[^}\n]+\}\}", content)))
    if leftovers:
        print(f"❌ 错误: 生成物存在未替换模板标记: {', '.join(leftovers)}", file=sys.stderr)
        print("   异常分类: PLACEHOLDER_LEFT", file=sys.stderr)
        print("   属生成器/模板 bug（勿提交含 {{...}} 的 Jenkinsfile），修复后重试", file=sys.stderr)
        return 1
    # 防呆: Groovy 单引号字符串不能跨行 —— defaultValue: '...' 必须在同一行闭合
    broken = [
        i + 1
        for i, line in enumerate(content.split("\n"))
        if "defaultValue: '" in line and not re.search(r"defaultValue: '[^']*'", line)
    ]
    if broken:
        print(f"❌ 错误: 第 {broken} 行 defaultValue 的单引号字符串跨行（Groovy 非法）", file=sys.stderr)
        print("   多行文本参数需转义为 \\n（生成器 groovy_single_quote()），否则 Jenkins 报 expecting \"'''\", found '\n'", file=sys.stderr)
        return 1
    # --- 校验模式 (提交门禁): 重生成比对，不落盘 ---
    if args.check or args.diff:
        return do_check(output, content, cfg, args.diff)

    with open(output, "w", encoding="utf-8") as f:
        f.write(content)
    if output.endswith(".sh"):
        os.chmod(output, 0o755)
    print(f"✅ Jenkinsfile 已生成 → {output}")

    # --- frontend-ssr: 同步输出 Next.js standalone Dockerfile (项目根, 与 Jenkinsfile 同目录) ---
    if cfg["type"] == "frontend-ssr":
        dockerfile_path = os.path.join(os.path.dirname(output) or ".", "Dockerfile")
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(render_ssr_dockerfile(cfg))
        print(f"✅ Dockerfile (standalone) 已生成 → {dockerfile_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
