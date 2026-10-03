pipeline {
    agent any

    // --- 参数化构建配置 ---
    parameters {
        choice(name: 'DEPLOY_ENV', choices: ['dev', 'prod', 'test'], description: '选择要部署的目标环境')

        // --- 服务器配置 ---
        string(name: 'DEV_SERVER_IP', defaultValue: '192.168.1.5', description: 'Dev 环境服务器 IP')
        string(name: 'PROD_SERVER_IP', defaultValue: '192.168.1.102', description: 'Prod 环境服务器 IP')
        string(name: 'SSH_USER', defaultValue: 'root', description: '部署服务器 SSH 用户名')

        // --- 应用基本信息 ---
        string(name: 'EXPOSE_APP_PORT', defaultValue: '8080', description: 'HTTP 应用暴露端口 (Host Port)')
        string(name: 'APP_NAME', defaultValue: 'cland-ws-gateway-service', description: '应用服务名称')
        string(name: 'APP_PORT', defaultValue: '8080', description: 'HTTP 应用内部监听端口 (Container Port)')
        string(name: 'WS_PORT', defaultValue: '8081', description: 'WebSocket 帧网关端口 (双端口服务, 额外映射)')
        string(name: 'HEALTH_PATH', defaultValue: '/api/health', description: '部署后健康检查路径 (HTTP GET)')

        // --- 仓库地址配置 ---
        string(name: 'DOCKER_REGISTRY', defaultValue: '192.168.1.7:5000', description: 'Docker 私有仓库上传地址')
        string(name: 'DOCKER_REGISTRY_CACHE', defaultValue: '192.168.1.7:5001', description: 'Docker 公共镜像缓存/拉取地址')

        // --- 版本控制 ---
        string(name: 'GO_VERSION', defaultValue: '1.24', description: 'Go 编译环境版本')

        // --- 凭证 ID 配置 ---
        string(name: 'GIT_CREDENTIAL_ID', defaultValue: 'chaineasy', description: 'Git 仓库访问凭证 ID')
        string(name: 'DOCKER_CREDENTIAL_ID', defaultValue: 'docker-registry-auth', description: 'Docker 仓库认证凭证 ID')
        string(name: 'DEPLOY_SSH_CRED_ID', defaultValue: 'deploy-server-ssh-key', description: '目标服务器部署 SSH 凭证 ID')
    }

    environment {
        CGO_ENABLED = "1"
        GOPROXY     = "https://goproxy.cn,direct"
        GOSUMDB     = "off"
        GOTOOLCHAIN = "local"
    }

    stages {
        stage('Checkout & Init') {
            steps {
                checkout scm
                script {
                    if (env.BRANCH_NAME == null || env.BRANCH_NAME == '') {
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
                    // Go 官方镜像（含 gcc，满足 CGO/mattn-go-sqlite3）；走 5001 缓存仓
                    // 镜像自带 registry 主机(5001 缓存仓, 匿名可拉)，不再设 registryUrl，避免二次前缀
                    image "${env.DOCKER_REGISTRY_CACHE}/library/golang:${env.GO_VERSION}-bookworm"
                    args ' -u 0:0 --entrypoint="" -e CGO_ENABLED=1 -e GOPROXY=https://goproxy.cn,direct -e GOSUMDB=off -e GOTOOLCHAIN=local -v /var/lib/jenkins/go_cache/mod:/go/pkg/mod -v /var/lib/jenkins/go_cache/build:/root/.cache/go-build'
                    reuseNode true
                }
            }
            steps {
                sshagent(credentials: ["${GIT_CREDENTIAL_ID}"]) {
                    script {
                        echo '--- 1. 质量门禁: go vet + go test ---'
                        sh 'go vet ./...'
                        sh 'go test ./...'

                        echo '--- 2. 构建二进制 (CGO) ---'
                        sh 'mkdir -p build && go build -buildvcs=false -o build/cland-ws-gateway .'
                    }
                }
            }
        }

        stage('Build & Push Docker Image') {
            when {
                expression {
                    return env.BRANCH_NAME =~ /release\/.*/ || env.BRANCH_NAME =~ /master|main/
                }
            }
            steps {
                script {
                    echo "Building Docker Image..."
                    def gitCommit = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
                    def imageTag = "${env.BUILD_NUMBER}-${gitCommit}"
                    def fullImageName = "${DOCKER_REGISTRY}/${APP_NAME}:${imageTag}"
                    def latestImageName = "${APP_NAME}:latest"

                    docker.withRegistry("http://${DOCKER_REGISTRY}", DOCKER_CREDENTIAL_ID) {
                        def app = docker.build(fullImageName, ".")
                        app.push()
                        sh "docker tag ${fullImageName} ${DOCKER_REGISTRY}/${latestImageName}"
                        sh "docker push ${DOCKER_REGISTRY}/${latestImageName}"
                    }
                }
            }
        }

        stage('Deploy Service') {
            when {
                expression {
                    return env.BRANCH_NAME =~ /release\/.*/ || env.BRANCH_NAME =~ /master|main/
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
                    def latestImageName = "${DOCKER_REGISTRY}/${APP_NAME}:latest"

                    def baseDir = "/opt/${APP_NAME}"
                    def logDir = "${baseDir}/logs"
                    def dataDir = "${baseDir}/data"

                    echo "Deploying to [${selectedEnv}] -> ${targetIp} using Path: ${baseDir}"

                    def remoteScript = """
                        set -e
                        mkdir -p ${logDir} ${dataDir}
                        chmod -R 777 ${logDir} ${dataDir}

                        docker pull ${latestImageName}

                        # 幂等替换旧容器：直接 rm -f
                        docker rm -f ${APP_NAME} >/dev/null 2>&1 || true

                        docker run -d \\
                            --restart=always \\
                            --name ${APP_NAME} \\
                            -p ${params.EXPOSE_APP_PORT}:${APP_PORT} \\
                            -p ${params.WS_PORT}:${params.WS_PORT} \\
                            -v ${logDir}:/app/logs \\
                            -v ${dataDir}:/app/data \\
                            -e CLAND_SERVER_PORT=${APP_PORT} \\
                            ${latestImageName}

                        docker ps -f name=${APP_NAME}
                    """

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
                        error "❌ 部署后健康检查失败: ${healthUrl}"
                    }
                    echo "✅ Deployment successful!（健康检查通过: ${healthUrl}）"
                }
            }
        }

        stage('Merge Back to Master') {
            when {
                expression { return env.BRANCH_NAME =~ /release\/.*/ }
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
                    }
                }
            }
        }
    }

    post {
        always {
            cleanWs()
            sh "docker image prune -f || true"
        }
        success { echo "Pipeline finished successfully." }
        failure { echo "Pipeline failed." }
    }
}
