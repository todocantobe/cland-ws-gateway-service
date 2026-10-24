pipeline {
    agent any

    // --- 参数化构建配置 (占位值由 generate-jenkinsfile.py 替换) ---
    parameters {
        choice(name: 'DEPLOY_ENV', choices: ['dev', 'prod', 'test'], description: '选择要部署的目标环境')
        booleanParam(name: 'APPROVE_PROD', defaultValue: false, description: 'prod 部署必须显式勾选（#682）')
        booleanParam(name: 'RUN_DEPLOY', defaultValue: false, description: '显式请求部署（默认 false：仅构建产镜像，不部署）')

        // --- 服务器配置 (生产建议在 Jenkins Job 参数默认值中维护) ---
        string(name: 'DEV_SERVER_IP', defaultValue: '192.168.1.5', description: 'Dev 环境服务器 IP')
        string(name: 'PROD_SERVER_IP', defaultValue: '192.168.1.102', description: 'Prod 环境服务器 IP')
        string(name: 'SSH_USER', defaultValue: 'root', description: '部署服务器 SSH 用户名')

        // --- 应用基本信息 ---
        string(name: 'EXPOSE_APP_PORT', defaultValue: '8080', description: 'HTTP 应用暴露端口 (Host Port)')
        string(name: 'APP_NAME', defaultValue: 'cland-ws-gateway-service', description: '应用服务名称')
        string(name: 'APP_PORT', defaultValue: '8080', description: 'HTTP 应用内部监听端口 (Container Port)')
        string(name: 'WS_PORT', defaultValue: '8081', description: 'WebSocket 端口 (双端口服务, 额外映射)')
        string(name: 'HEALTH_PATH', defaultValue: '/health', description: '部署后健康检查路径 (HTTP GET)')

        // --- 仓库地址配置 ---
        string(name: 'DOCKER_REGISTRY', defaultValue: '192.168.1.7:5000', description: 'Docker 私有仓库上传地址')
        string(name: 'DOCKER_REGISTRY_CACHE', defaultValue: '192.168.1.7:5001', description: 'Docker 公共镜像缓存/拉取地址')

        // --- 版本控制 ---
        string(name: 'GO_VERSION', defaultValue: '1.24', description: 'Go 编译环境版本')

        // --- Nacos 配置参数 (服务未接入 Nacos 时可忽略) ---
        string(name: 'NACOS_SERVERADDR', defaultValue: '192.168.1.11:8848', description: 'Nacos 服务器地址 (格式: host:port)')
        string(name: 'NACOS_NAMESPACE', defaultValue: 'ec1145fa-c136-470b-b94b-4fa365312a3d', description: 'Nacos 命名空间 (public 留空)')
        string(name: 'NACOS_DATAID', defaultValue: 'cland-ws-gateway-service', description: 'Nacos 配置 Data ID')
        string(name: 'NACOS_GROUP', defaultValue: 'DEFAULT_GROUP', description: 'Nacos 配置 Group')
        string(name: 'NACOS_USERNAME', defaultValue: 'base', description: 'Nacos 用户名')
        string(name: 'NACOS_CREDENTIAL_ID', defaultValue: 'nacos-base-creds', description: 'Nacos 密码凭据 ID (usernamePassword；密码不落明文)')

        // --- 凭证 ID 配置 ---
        string(name: 'GIT_CREDENTIAL_ID', defaultValue: 'jenkins_github', description: 'Git 仓库访问凭证 ID')
        string(name: 'DOCKER_CREDENTIAL_ID', defaultValue: 'docker-registry-auth', description: 'Docker 仓库认证凭证 ID')
        string(name: 'DEPLOY_SSH_CRED_ID', defaultValue: 'deploy-server-ssh-key', description: '目标服务器部署 SSH 凭证 ID')
        string(name: 'IMAGE_DIGEST_OVERRIDE', defaultValue: '', description: '回滚用：指定 image@sha256 摘要（留空=用本次构建产物）；#540 digest 钉住')
    }

    environment {
        CGO_ENABLED = "1"
        GOPROXY     = "https://goproxy.cn,direct"
        GOSUMDB     = "off"
        GOTOOLCHAIN = "local"
    }

    stages {
        // #682 DEPLOY_ENV 校验（默认非 prod；非法值必失败；prod 需显式勾 APPROVE_PROD）
        // 判据来源: base/cland-crawler#682。校验逻辑只有这一份，ci 测试原样抽取此块运行。
        stage('Validate DEPLOY_ENV') {
            steps {
                sh '''
                # >>> deploy-env-check（test-deploy-env.sh 抽取边界，勿删）
                set -eu
                v="$(printf '%s' "${DEPLOY_ENV:-}" | tr -d '[:space:]')"
                case "$v" in
                  '')
                    echo "❌ DEPLOY_ENV 为空：必须显式选择（允许：staging/dev/test/prod）" >&2; exit 1 ;;
                  staging|dev|test)
                    echo "✅ DEPLOY_ENV=$v" ;;
                  prod)
                    if [ "${APPROVE_PROD:-false}" != "true" ]; then
                      echo "❌ DEPLOY_ENV=prod 必须同时勾选 APPROVE_PROD（显式审批）" >&2; exit 1
                    fi
                    echo "✅ DEPLOY_ENV=prod（已审批）" ;;
                  *)
                    echo "❌ DEPLOY_ENV 非法：'$v'（允许：staging/dev/test/prod）" >&2; exit 1 ;;
                esac
                # <<< deploy-env-check
                '''
            }
        }

        stage('Checkout & Init') {
            steps {
                checkout scm
                script {
                    // 发布分支固定值（可选）：配置了 release_branch 则覆盖 SCM 选出的分支；否则用 Jenkins 原生 pick
                    def fixedRb = "".trim()
                    if (fixedRb) {
                        sh "git fetch -q origin ${fixedRb} && git checkout -f ${fixedRb}"
                        env.BRANCH_NAME = fixedRb
                    } else if (env.BRANCH_NAME == null || env.BRANCH_NAME == '') {
                        if (env.GIT_BRANCH != null) {
                            env.BRANCH_NAME = env.GIT_BRANCH.replace('origin/', '')
                        } else {
                            env.BRANCH_NAME = sh(returnStdout: true, script: 'git rev-parse --abbrev-ref HEAD').trim()
                        }
                    }
                    echo "Current Branch: ${env.BRANCH_NAME}"
                }
            }
        }

        stage('Go Quality & Build') {
            agent {
                docker {
                    // Go 官方镜像（含 gcc，满足 CGO）；镜像自带 registry 主机(5001 缓存仓, 匿名可拉)，
                    // 不再设 registryUrl，避免二次前缀
                    image "${env.DOCKER_REGISTRY_CACHE}/library/golang:${env.GO_VERSION}-bookworm"
                    args ' -u 0:0 --entrypoint="" -e CGO_ENABLED=1 -e GOPROXY=https://goproxy.cn,direct -e GOSUMDB=off -e GOTOOLCHAIN=local -v /var/lib/jenkins/go_cache/mod:/go/pkg/mod -v /var/lib/jenkins/go_cache/build:/root/.cache/go-build'
                    reuseNode true
                }
            }
            steps {
                sshagent(credentials: ["${GIT_CREDENTIAL_ID}"]) {
                    script {
                        try {
                        echo '--- 1. 质量门禁: 统一入口 scripts/check.sh (Jenkins/本地同源, #397) ---'
                        sh '''
                            if [ -f scripts/check.sh ]; then
                                bash scripts/check.sh --stage all
                            else
                                echo "⚠️ 无 scripts/check.sh，回退内置门禁（请由 harbor 模板补齐统一入口, #397）"
                                go vet ./...
                                go test ./...
                            fi
                        '''

                        echo '--- 2. 构建二进制 (CGO, 产物 build/app) ---'
                        // -buildvcs=false: 容器内 git ownership 导致 VCS stamping exit 128
                        sh 'mkdir -p build && go build -buildvcs=false -o build/app .'
                        } finally {
                            // 容器以 root 运行 → 产物 root 属主；交还 workspace 属主，保证 post rm 可删
                            sh 'own=$(stat -c %u:%g . 2>/dev/null || echo 0:0); chown -R "$own" . 2>/dev/null || true'
                        }
                    }
                }
            }
        }

        stage('Build & Push Docker Image') {
            when {
                expression {
                    return env.BRANCH_NAME =~ /release\/.*/ || env.BRANCH_NAME =~ /hotfix\/.*/ || env.BRANCH_NAME =~ /master|main/
                }
            }
            steps {
                script {
                    echo "Building Docker Image..."
                    def gitCommit = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
                    def imageTag = "${env.BUILD_NUMBER}-${gitCommit}"
                    def fullImageName = "${DOCKER_REGISTRY}/${APP_NAME}:${imageTag}"

                    docker.withRegistry("http://${DOCKER_REGISTRY}", DOCKER_CREDENTIAL_ID) {
                        def app = docker.build(fullImageName, ".")
                        app.push()
                    }
                    // #540 制品 digest 钉住：解析推送后的不可变摘要（部署以 image@sha256 引用）
                    env.IMAGE_DIGEST = sh(returnStdout: true, script: "docker image inspect ${fullImageName} | tr -d '[:space:]' | grep -oE '@sha256:[0-9a-f]{64}' | head -1 | cut -c2-").trim()
                    if (!(env.IMAGE_DIGEST ==~ /sha256:[0-9a-f]{64}/)) {
                        error "❌ 无法解析镜像 digest（得到: '${env.IMAGE_DIGEST}'）——#540 digest 钉住"
                    }
                    writeFile file: '.image-digest', text: env.IMAGE_DIGEST + "\n"
                    echo "--- 镜像 digest = ${env.IMAGE_DIGEST} ---"
                }
            }
        }

        stage('Deploy Gates (G0–G3 · #398)') {
            when {
                expression { return params.RUN_DEPLOY == true }
            }
            steps {
                script {
                    sh '''
                        if [ -f scripts/deploy-gate.sh ]; then
                            echo "--- 部署门禁 G0-G3 (env=${DEPLOY_ENV}, branch=${BRANCH_NAME}) ---"
                            bash scripts/deploy-gate.sh --env "${DEPLOY_ENV}" --branch "${BRANCH_NAME}" --run-deploy "${RUN_DEPLOY}"
                        else
                            echo "❌ 缺部署门禁脚本 scripts/deploy-gate.sh（#398，install-jenkinsfile-gate.sh 安装）"; exit 1
                        fi
                    '''
                }
            }
        }

        stage('Deploy Service') {
            when {
                expression {
                    return params.RUN_DEPLOY == true && (env.BRANCH_NAME =~ /release\/.*/ || env.BRANCH_NAME =~ /hotfix\/.*/ || env.BRANCH_NAME =~ /master|main/)
                }
            }
            steps {
                script {
                    def serverConfig = [
                        'dev':   [ip: params.DEV_SERVER_IP,   user: params.SSH_USER],
                        'prod':  [ip: params.PROD_SERVER_IP,  user: params.SSH_USER],
                        'test':  [ip: params.DEV_SERVER_IP,   user: params.SSH_USER]
                    ]
                    def selectedEnv = params.DEPLOY_ENV
                    def targetConfig = serverConfig[selectedEnv]
                    if (!targetConfig) {
                        error "环境 ${selectedEnv} 未定义！"
                    }
                    def targetIp = targetConfig.ip
                    def sshUser = targetConfig.user
                    // #540 digest 钉住（含回滚覆盖）：部署 image@sha256，弃 :latest
                    def imageDigest = (params.IMAGE_DIGEST_OVERRIDE?.trim()) ?: (env.IMAGE_DIGEST ?: readFile('.image-digest').trim())
                    if (!(imageDigest ==~ /sha256:[0-9a-f]{64}/)) { error "❌ digest 非法（得到: '${imageDigest}'）——#540" }
                    def pinnedImageName = "${DOCKER_REGISTRY}/${APP_NAME}@${imageDigest}"

                    def baseDir = "/opt/${APP_NAME}"
                    def logDir = "${baseDir}/logs"
                    def dataDir = "${baseDir}/data"

                    echo "Deploying to [${selectedEnv}] -> ${targetIp} using Path: ${baseDir}"

                    def remoteScript = """
                        set -e
                        sudo mkdir -p ${logDir} ${dataDir}
                        sudo chmod -R 777 ${logDir} ${dataDir}

                        docker pull ${pinnedImageName}

                        # 幂等替换旧容器：直接 rm -f（stop+rm 在 restarting 异常态会失败）
                        docker rm -f ${APP_NAME} >/dev/null 2>&1 || true

                        docker run -d \\
                            --restart=always \\
                            --name ${APP_NAME} \\
                            -p ${params.EXPOSE_APP_PORT}:${APP_PORT} \\
                            -p ${params.WS_PORT}:${params.WS_PORT} \\
                            -v ${logDir}:/app/logs \\
                            -v ${dataDir}:/app/data \\
                            -e CLAND_SERVER_PORT=${APP_PORT} \\
                            -e NACOS_SERVERADDR="${params.NACOS_SERVERADDR}" \\
                            -e NACOS_NAMESPACE="${params.NACOS_NAMESPACE}" \\
                            -e NACOS_DATAID="${params.NACOS_DATAID}" \\
                            -e NACOS_GROUP="${params.NACOS_GROUP}" \\
                            -e NACOS_USERNAME="${params.NACOS_USERNAME}" \\
                            -e NACOS_PASSWORD="\$NACOS_CRED_PASS" \\
                            ${pinnedImageName}

                        docker ps -f name=${APP_NAME}
                    """

                    withCredentials([usernamePassword(credentialsId: params.NACOS_CREDENTIAL_ID, usernameVariable: 'NACOS_CRED_BIND_USER', passwordVariable: 'NACOS_CRED_PASS')]) {
                    withCredentials([usernamePassword(credentialsId: env.DOCKER_CREDENTIAL_ID, usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                        def loginCmd = "docker login ${DOCKER_REGISTRY} -u \$DOCKER_USER -p \$DOCKER_PASS"
                        if (targetIp == '127.0.0.1' || targetIp == 'localhost') {
                            sh "export DOCKER_USER='\$DOCKER_USER' DOCKER_PASS='\$DOCKER_PASS'; ${loginCmd} && ${remoteScript}"
                        } else {
                            withCredentials([sshUserPrivateKey(credentialsId: env.DEPLOY_SSH_CRED_ID, keyFileVariable: 'SSH_KEY_FILE')]) {
                                sh """
                                    ssh -o StrictHostKeyChecking=no -i \$SSH_KEY_FILE ${sshUser}@${targetIp} "
                                        export DOCKER_USER='\$DOCKER_USER' DOCKER_PASS='\$DOCKER_PASS';
                                        ${loginCmd} && ${remoteScript}
                                    "
                                """
                            }
                        }
                    }
                    }

                    // 部署后健康检查（失败即构建失败，杜绝“假成功”）
                    def healthUrl = "http://${targetIp}:${params.EXPOSE_APP_PORT}${params.HEALTH_PATH}"
                    echo "健康检查: ${healthUrl}"
                    def healthy = false
                    for (int i = 1; i <= 20; i++) {
                        def code = sh(script: "curl -s -o /dev/null -w '%{http_code}' -m 5 '${healthUrl}' || true", returnStdout: true).trim()
                        if (code == '200') { healthy = true; break }
                        sleep 3
                    }
                    if (!healthy) {
                        error "❌ 部署后健康检查失败: ${healthUrl}（容器未起 / 端口不通 / 镜像拉取失败）"
                    }
                    echo "✅ Deployment successful!（健康检查通过: ${healthUrl}）"
                }
            }
        }

        stage('Post-Deploy Self-Check (G5 · #398)') {
            when {
                expression { return params.RUN_DEPLOY == true && (env.BRANCH_NAME =~ /release\/.*/ || env.BRANCH_NAME =~ /hotfix\/.*/ || env.BRANCH_NAME =~ /master|main/) }
            }
            steps {
                script {
                    def scServerConfig = ['dev': params.DEV_SERVER_IP, 'prod': params.PROD_SERVER_IP, 'test': params.DEV_SERVER_IP]
                    def scTargetIp = scServerConfig[params.DEPLOY_ENV]
                    sh """
                        if [ -f scripts/selfcheck.sh ]; then
                            bash scripts/selfcheck.sh --base http://${scTargetIp}:${params.EXPOSE_APP_PORT} --health-path ${params.HEALTH_PATH}
                        else
                            echo "❌ 缺部署后自证脚本 scripts/selfcheck.sh（#398）"; exit 1
                        fi
                    """
                }
            }
        }

        stage('Merge Back to Master') {
            when {
                // #634: Merge Back = 「改主线」动作，必须与「本次是部署」绑定（RUN_DEPLOY=true）。
                // 只构建取证(RUN_DEPLOY=false) ⇒ 不得合并/推送主线；判据：构建前后 origin/<主line> 不变。
                expression { return params.RUN_DEPLOY == true && (env.BRANCH_NAME =~ /release\/.*/ || env.BRANCH_NAME =~ /hotfix\/.*/) }
            }
            steps {
                sshagent(credentials: ["${GIT_CREDENTIAL_ID}"]) {
                    script {
                        echo "Merging ${env.BRANCH_NAME} into master..."
                        sh 'git config core.sshCommand "ssh -o StrictHostKeyChecking=no"'
                        sh 'git config user.email "jenkins@example.com"'
                        sh 'git config user.name "Jenkins CI"'
                        sh 'git fetch origin master'
                        sh 'git checkout master'
                        sh 'git pull origin master'
                        sh "git merge --no-ff origin/${env.BRANCH_NAME} -m 'Merge release ${env.BRANCH_NAME} to master [Jenkins Build #${env.BUILD_NUMBER}]'"
                        sh 'git push origin master'
                        // #576: Merge Back 后强制回灌 develop（master ⊆ develop），不靠人记得
                        def backfillExists = (sh(script: 'git ls-remote --exit-code --heads origin develop >/dev/null 2>&1', returnStatus: true) == 0)
                        if (backfillExists) {
                            echo "回灌 master -> develop（#576）..."
                            sh 'git fetch origin develop master'
                            sh 'git checkout develop'
                            sh 'git pull --ff-only origin develop'
                            sh "git merge --no-ff origin/master -m 'Merge master back into develop [Jenkins Build #${env.BUILD_NUMBER}] (#576)'"
                            sh 'git push origin develop'
                            def ancestryRc = sh(script: 'git merge-base --is-ancestor origin/master origin/develop', returnStatus: true)
                            if (ancestryRc != 0) { error "❌ 回灌校验失败：master ⊄ develop（#576 镜像守卫）" }
                            echo "✅ 已回灌：master ⊆ develop"
                        } else {
                            echo "⚠️ 远端无 develop（本仓未启用 git-flow 双向），跳过回灌（#576）"
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            // 用 shell 快删替代 cleanWs()（避免 ws-cleanup 异步残留堆积，见 docs/ops/Jenkins-workspace-膨胀治理-操作手册.md）
            sh 'rm -rf -- * .[^.]* || true'
            sh "docker image prune -f || true"
        }
        success { echo "Pipeline finished successfully." }
        failure { echo "Pipeline failed." }
    }
}
