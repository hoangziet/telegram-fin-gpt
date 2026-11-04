targetScope = 'resourceGroup'

@minLength(3)
@maxLength(12)
@description('The same lowercase workload prefix used by main.bicep.')
param namePrefix string

@allowed([
  'dev'
  'test'
  'prod'
])
param environmentName string

@description('The same Azure region used by the shared platform template.')
param location string

@description('Immutable image reference already pushed to the private ACR.')
param backendImage string

@description('Versionless or pinned Key Vault URI for the least-privilege application DATABASE_URL.')
param databaseUrlSecretUri string

@description('Versionless or pinned Key Vault URI for FIN_GPT_SECRET_KEY.')
param sessionKeySecretUri string

@description('Maximum API replicas chosen for the expected workload.')
@minValue(1)
@maxValue(100)
param apiMaxReplicas int

@description('Maximum worker replicas chosen for the expected workload.')
@minValue(1)
@maxValue(100)
param workerMaxReplicas int

@description('Key Vault-backed API-only environment secrets, such as Telegram webhook secrets.')
param apiRuntimeSecrets array = []

@description('Key Vault-backed worker-only environment secrets, such as Telegram bot tokens.')
param workerRuntimeSecrets array = []

@description('Create the migration job before applying database migrations.')
param createMigrationJob bool = true

@description(
  'Create a private manual job that verifies restored PostgreSQL and application readiness without reading rows.'
)
param createRestoreVerificationJob bool = true

@description('Create API and worker apps only after the migration job has succeeded.')
param deployRuntimeApps bool = false

var apiName = '${namePrefix}-${environmentName}-api'
var workerName = '${namePrefix}-${environmentName}-worker'
var foundryAccountName = '${namePrefix}${environmentName}ai'
var migrationJobName = '${namePrefix}-${environmentName}-migrate'
var restoreVerificationJobName = '${namePrefix}-${environmentName}-rverify'
var queueName = 'fingpt-events'
var serviceBusNamespaceFqdn = replace(
  replace(
    replace(
      replace(serviceBus.properties.serviceBusEndpoint, 'https://', ''),
      'sb://',
      ''
    ),
    ':443/',
    ''
  ),
  '/',
  ''
)
var apiRuntimeSecretEntries = [for secret in apiRuntimeSecrets: {
  name: secret.name
  keyVaultUrl: secret.keyVaultUrl
  identity: apiIdentity.id
}]
var workerRuntimeSecretEntries = [for secret in workerRuntimeSecrets: {
  name: secret.name
  keyVaultUrl: secret.keyVaultUrl
  identity: workerIdentity.id
}]
var apiRuntimeEnvironment = [for secret in apiRuntimeSecrets: {
  name: secret.environmentVariable
  secretRef: secret.name
}]
var workerRuntimeEnvironment = [for secret in workerRuntimeSecrets: {
  name: secret.environmentVariable
  secretRef: secret.name
}]
var apiSecretEntries = concat(
  [
    {
      name: 'database-url'
      keyVaultUrl: databaseUrlSecretUri
      identity: apiIdentity.id
    }
    {
      name: 'session-key'
      keyVaultUrl: sessionKeySecretUri
      identity: apiIdentity.id
    }
  ],
  apiRuntimeSecretEntries
)
var workerSecretEntries = concat(
  [
    {
      name: 'database-url'
      keyVaultUrl: databaseUrlSecretUri
      identity: workerIdentity.id
    }
  ],
  workerRuntimeSecretEntries
)
var apiEnvironment = concat(
  [
    { name: 'DATABASE_URL', secretRef: 'database-url' }
    { name: 'FIN_GPT_SECRET_KEY', secretRef: 'session-key' }
    { name: 'FINGPT_DEPLOYMENT_ENV', value: 'production' }
    { name: 'AUTH_COOKIE_SECURE', value: 'true' }
    { name: 'FINGPT_EVIDENCE_STORAGE_MODE', value: 'azure' }
    { name: 'FINGPT_EVIDENCE_BLOB_ACCOUNT_URL', value: evidenceStorage.properties.primaryEndpoints.blob }
    { name: 'FINGPT_EVIDENCE_BLOB_CONTAINER', value: 'evidence' }
    { name: 'FINGPT_FOUNDRY_OPENAI_BASE_URL', value: 'https://${foundryAccountName}.openai.azure.com/openai/v1' }
    { name: 'FINGPT_FOUNDRY_MODEL', value: 'gpt-4.1-mini' }
    { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
  ],
  apiRuntimeEnvironment
)
var workerEnvironment = concat(
  [
    { name: 'DATABASE_URL', secretRef: 'database-url' }
    { name: 'FINGPT_DEPLOYMENT_ENV', value: 'production' }
    { name: 'SERVICE_BUS_NAMESPACE', value: serviceBusNamespaceFqdn }
    { name: 'SERVICE_BUS_QUEUE_NAME', value: queueName }
    { name: 'AZURE_CLIENT_ID', value: workerIdentity.properties.clientId }
  ],
  workerRuntimeEnvironment
)

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: '${namePrefix}-${environmentName}-aca'
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: '${namePrefix}${environmentName}acr'
}

resource evidenceStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: '${namePrefix}${environmentName}evidence'
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = {
  name: '${namePrefix}-${environmentName}-servicebus'
}

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${namePrefix}-${environmentName}-api-id'
}

resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${namePrefix}-${environmentName}-worker-id'
}

resource operationsActionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: '${namePrefix}-${environmentName}-alerts'
}

resource imagePullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${namePrefix}-${environmentName}-pull-id'
}

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = if (createMigrationJob) {
  name: migrationJobName
  location: location
  tags: {
    Project: 'FinGPT'
    Environment: environmentName
    Purpose: 'Manual database migration'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiIdentity.id}': {}
      '${imagePullIdentity.id}': {}
    }
  }
  properties: {
    environmentId: acaEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: imagePullIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseUrlSecretUri
          identity: apiIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: backendImage
          command: [ 'alembic' ]
          args: [ '-c', '/app/alembic.ini', 'upgrade', 'head' ]
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
          ]
          resources: {
            cpu: 1
            memory: '2Gi'
          }
        }
      ]
    }
  }
}

resource restoreVerificationJob 'Microsoft.App/jobs@2024-03-01' = if (createRestoreVerificationJob) {
  name: restoreVerificationJobName
  location: location
  tags: {
    Project: 'FinGPT'
    Environment: environmentName
    Purpose: 'Read-only restored database and API readiness verification'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiIdentity.id}': {}
      '${imagePullIdentity.id}': {}
    }
  }
  properties: {
    environmentId: acaEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: imagePullIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseUrlSecretUri
          identity: apiIdentity.id
        }
        {
          name: 'session-key'
          keyVaultUrl: sessionKeySecretUri
          identity: apiIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'restore-verifier'
          image: backendImage
          command: [ 'python', '-m', 'fingpt.restore_verification' ]
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'FIN_GPT_SECRET_KEY', secretRef: 'session-key' }
            { name: 'FINGPT_DEPLOYMENT_ENV', value: 'production' }
            { name: 'FINGPT_EVIDENCE_STORAGE_MODE', value: 'azure' }
            { name: 'FINGPT_EVIDENCE_BLOB_ACCOUNT_URL', value: evidenceStorage.properties.primaryEndpoints.blob }
    { name: 'FINGPT_EVIDENCE_BLOB_CONTAINER', value: 'evidence' }
    { name: 'FINGPT_FOUNDRY_OPENAI_BASE_URL', value: 'https://${foundryAccountName}.openai.azure.com/openai/v1' }
    { name: 'FINGPT_FOUNDRY_MODEL', value: 'gpt-4.1-mini' }
    { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
          ]
          resources: {
            cpu: 1
            memory: '2Gi'
          }
        }
      ]
    }
  }
}

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = if (deployRuntimeApps) {
  name: apiName
  location: location
  tags: {
    Project: 'FinGPT'
    Environment: environmentName
    Component: 'API'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiIdentity.id}': {}
      '${imagePullIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: imagePullIdentity.id
        }
      ]
      secrets: apiSecretEntries
    }
    template: {
      containers: [
        {
          name: 'api'
          image: backendImage
          env: apiEnvironment
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health/live', port: 8080, scheme: 'HTTP' }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health/ready', port: 8080, scheme: 'HTTP' }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
          resources: {
            cpu: 1
            memory: '2Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

resource workerApp 'Microsoft.App/containerApps@2024-03-01' = if (deployRuntimeApps) {
  name: workerName
  location: location
  tags: {
    Project: 'FinGPT'
    Environment: environmentName
    Component: 'worker'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workerIdentity.id}': {}
      '${imagePullIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registry.properties.loginServer
          identity: imagePullIdentity.id
        }
      ]
      secrets: workerSecretEntries
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: backendImage
          command: [ 'python', '-m', 'fingpt.worker' ]
          env: workerEnvironment
          resources: {
            cpu: 1
            memory: '2Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: workerMaxReplicas
        rules: [
          {
            name: 'cpu-utilization'
            custom: {
              type: 'cpu'
              metadata: {
                type: 'Utilization'
                value: '75'
              }
            }
          }
        ]
      }
    }
  }
}

resource apiServerErrorsAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (deployRuntimeApps) {
  name: '${namePrefix}-${environmentName}-api-5xx'
  location: 'global'
  tags: {
    Project: 'FinGPT'
    Environment: environmentName
  }
  properties: {
    description: 'At least five API server errors occurred during a five-minute window.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    scopes: [apiApp.id]
    actions: [{ actionGroupId: operationsActionGroup.id }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'ApiServerErrors'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerapps'
          operator: 'GreaterThanOrEqual'
          threshold: 5
          timeAggregation: 'Total'
          skipMetricValidation: false
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: ['5xx']
            }
          ]
        }
      ]
    }
  }
}

resource workerUnavailableAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (deployRuntimeApps) {
  name: '${namePrefix}-${environmentName}-worker-down'
  location: 'global'
  tags: {
    Project: 'FinGPT'
    Environment: environmentName
  }
  properties: {
    description: 'The worker Container App has had fewer than one replica for five minutes.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    scopes: [workerApp.id]
    actions: [{ actionGroupId: operationsActionGroup.id }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'WorkerReplicaCount'
          metricName: 'Replicas'
          metricNamespace: 'Microsoft.App/containerapps'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Average'
          skipMetricValidation: false
          dimensions: []
        }
      ]
    }
  }
}

output migrationJobName string = createMigrationJob ? migrationJobName : ''
output apiAppName string = deployRuntimeApps ? apiName : ''
output workerAppName string = deployRuntimeApps ? workerName : ''
output runtimeAlertNames array = deployRuntimeApps ? [
  apiServerErrorsAlert.name
  workerUnavailableAlert.name
] : []
output serviceBusNamespace string = serviceBusNamespaceFqdn
output evidenceBlobUrl string = evidenceStorage.properties.primaryEndpoints.blob
