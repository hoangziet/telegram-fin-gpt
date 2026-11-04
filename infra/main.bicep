targetScope = 'resourceGroup'

@minLength(3)
@maxLength(12)
@description('Lowercase alphanumeric workload prefix used in globally unique names.')
param namePrefix string = 'wmbg'

@allowed([
  'dev'
  'test'
  'prod'
])
param environmentName string = 'dev'

@description('Workload owner tag, such as an email address or team name.')
param owner string = 'project-owner'

@description('Email recipient for Azure Monitor action group notifications.')
param alertEmailAddress string = ''

@description('Azure region. Must be selected by the owner before deployment.')
param location string = 'southeastasia'

@description('Azure Static Web Apps control-plane region; Southeast Asia is not supported.')
param staticWebAppLocation string = 'eastasia'

@description('VNet CIDR selected for Container Apps and NAT-based stable egress.')
param vnetAddressPrefix string = '10.40.0.0/16'

@description('Dedicated Container Apps workload-profile subnet CIDR, sized for planned scale.')
param acaSubnetPrefix string = '10.40.0.0/23'

@description('PostgreSQL administrator login. Provide securely at deployment time.')
@minLength(1)
param postgresAdministratorLogin string = 'fingptadmin'

@secure()
@description('PostgreSQL administrator password. Never store this in parameter files.')
@minLength(16)
param postgresAdministratorPassword string

@description('PostgreSQL backup retention in days, selected by the owner.')
@minValue(7)
@maxValue(35)
param postgresBackupRetentionDays int = 7

@description('Evidence retention in days. Must be selected before production evidence is stored.')
@minValue(1)
@maxValue(3650)
param evidenceRetentionDays int = 30

@description('Globally unique, lowercase Azure OpenAI account name.')
@minLength(3)
@maxLength(64)
param foundryAccountName string = '${namePrefix}${environmentName}ai'

var commonTags = {
  Project: 'Where my bucks go?'
  Environment: environmentName
  Owner: owner
  DataClassification: 'Confidential'
}
var vnetName = '${namePrefix}-${environmentName}-vnet'
var queueName = 'fingpt-events'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${namePrefix}-${environmentName}-logs'
  location: location
  tags: commonTags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource acaNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${namePrefix}-${environmentName}-aca-nsg'
  location: location
  tags: commonTags
  properties: {
    securityRules: []
  }
}

resource natPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${namePrefix}-${environmentName}-nat-ip'
  location: location
  tags: commonTags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static', publicIPAddressVersion: 'IPv4' }
}

resource natGateway 'Microsoft.Network/natGateways@2023-09-01' = {
  name: '${namePrefix}-${environmentName}-nat'
  location: location
  tags: commonTags
  sku: { name: 'Standard' }
  properties: {
    idleTimeoutInMinutes: 10
    publicIpAddresses: [{ id: natPublicIp.id }]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: commonTags
  properties: {
    addressSpace: { addressPrefixes: [ vnetAddressPrefix ] }
    subnets: [
      {
        name: 'aca-infrastructure'
        properties: {
          addressPrefix: acaSubnetPrefix
          networkSecurityGroup: { id: acaNsg.id }
          natGateway: { id: natGateway.id }
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${namePrefix}-${environmentName}-pg'
  location: location
  tags: commonTags
    sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: postgresAdministratorLogin
    administratorLoginPassword: postgresAdministratorPassword
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: postgresBackupRetentionDays
      geoRedundantBackup: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    highAvailability: { mode: 'Disabled' }
    version: '16'
  }
}

resource postgresAcaFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: postgres
  name: 'container-apps-nat-egress'
  properties: {
    startIpAddress: natPublicIp.properties.ipAddress
    endIpAddress: natPublicIp.properties.ipAddress
  }
}

resource evidenceStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${namePrefix}${environmentName}evidence'
  location: location
  tags: commonTags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Allow'
    }
  }
}

resource evidenceContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${evidenceStorage.name}/default/evidence'
  properties: {
    publicAccess: 'None'
  }
}

resource evidenceLifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: evidenceStorage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          enabled: true
          name: 'delete-evidence-after-approved-retention'
          type: 'Lifecycle'
          definition: {
            actions: {
              baseBlob: {
                delete: { daysAfterModificationGreaterThan: evidenceRetentionDays }
              }
            }
            filters: {
              blobTypes: [ 'blockBlob' ]
              prefixMatch: [ 'users/' ]
            }
          }
        }
      ]
    }
  }
}





resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${namePrefix}${environmentName}acr'
  location: location
  tags: commonTags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}





resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: '${namePrefix}-${environmentName}-servicebus'
  location: location
  tags: commonTags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    minimumTlsVersion: '1.2'
  }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: queueName
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
  }
}









resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${namePrefix}-${environmentName}-kv'
  location: location
  tags: commonTags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
    networkAcls: { bypass: 'AzureServices', defaultAction: 'Allow' }
  }
}

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-${environmentName}-api-id'
  location: location
  tags: commonTags
}

resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-${environmentName}-worker-id'
  location: location
  tags: commonTags
}

resource imagePullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-${environmentName}-pull-id'
  location: location
  tags: commonTags
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-${environmentName}-appi'
  location: location
  tags: commonTags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
  }
}

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-${environmentName}-aca'
  location: location
  tags: commonTags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'aca-infrastructure')
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource staticWebApp 'Microsoft.Web/staticSites@2022-09-01' = {
  name: '${namePrefix}-${environmentName}-swa'
  location: staticWebAppLocation
  tags: commonTags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
  }
}

resource operationsActionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${namePrefix}-${environmentName}-alerts'
  location: 'global'
  tags: commonTags
  properties: {
    enabled: true
    groupShortName: 'Fin${environmentName}'
    emailReceivers: empty(alertEmailAddress) ? [] : [
      {
        name: 'FinGPT operations'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

resource postgresCpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-${environmentName}-postgres-cpu'
  location: 'global'
  tags: commonTags
  properties: {
    description: 'PostgreSQL CPU utilization stayed above 85% for five minutes.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    scopes: [postgres.id]
    actions: [{ actionGroupId: operationsActionGroup.id }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'HighPostgresCpu'
          metricName: 'cpu_percent'
          metricNamespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
          operator: 'GreaterThan'
          threshold: 85
          timeAggregation: 'Average'
          skipMetricValidation: false
          dimensions: []
        }
      ]
    }
  }
}

resource postgresStorageAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-${environmentName}-postgres-storage'
  location: 'global'
  tags: commonTags
  properties: {
    description: 'PostgreSQL storage utilization stayed above 80% for five minutes.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    scopes: [postgres.id]
    actions: [{ actionGroupId: operationsActionGroup.id }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'HighPostgresStorage'
          metricName: 'storage_percent'
          metricNamespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
          operator: 'GreaterThan'
          threshold: 80
          timeAggregation: 'Average'
          skipMetricValidation: false
          dimensions: []
        }
      ]
    }
  }
}

resource deadLetterAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-${environmentName}-queue-deadletters'
  location: 'global'
  tags: commonTags
  properties: {
    description: 'At least one message is present in the FinGPT queue dead-letter subqueue.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    scopes: [serviceBus.id]
    actions: [{ actionGroupId: operationsActionGroup.id }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'FinGPTQueueDeadLetters'
          metricName: 'DeadletteredMessages'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Maximum'
          skipMetricValidation: false
          dimensions: [
            {
              name: 'EntityName'
              operator: 'Include'
              values: [queueName]
            }
          ]
        }
      ]
    }
  }
}

resource queueBacklogAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-${environmentName}-queue-backlog'
  location: 'global'
  tags: commonTags
  properties: {
    description: 'Average active messages exceeded 100 during a ten-minute window.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT10M'
    autoMitigate: true
    scopes: [serviceBus.id]
    actions: [{ actionGroupId: operationsActionGroup.id }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'FinGPTQueueBacklog'
          metricName: 'ActiveMessages'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          operator: 'GreaterThan'
          threshold: 100
          timeAggregation: 'Average'
          skipMetricValidation: false
          dimensions: [
            {
              name: 'EntityName'
              operator: 'Include'
              values: [queueName]
            }
          ]
        }
      ]
    }
  }
}

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: foundryAccountName
  location: location
  tags: commonTags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: foundryAccountName
    publicNetworkAccess: 'Enabled'
  }
}

resource chatModel 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: 'gpt-4.1-mini'
  sku: {
    name: 'GlobalStandard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output resourceNames object = {
  logAnalytics: logAnalytics.name
  virtualNetwork: vnet.name
  postgresql: postgres.name
  storageAccount: evidenceStorage.name
  serviceBusNamespace: serviceBus.name
  serviceBusQueue: queue.name
  keyVault: keyVault.name
  containerRegistry: acr.name
  containerAppsEnvironment: acaEnvironment.name
  staticWebApp: staticWebApp.name
  staticWebAppUrl: 'https://${staticWebApp.properties.defaultHostname}'
  applicationInsights: appInsights.name
  operationsActionGroup: operationsActionGroup.name
  metricAlerts: [
    postgresCpuAlert.name
    postgresStorageAlert.name
    deadLetterAlert.name
    queueBacklogAlert.name
  ]
  apiIdentity: apiIdentity.name
  workerIdentity: workerIdentity.name
  imagePullIdentity: imagePullIdentity.name
  foundry: foundry.name
  chatModelDeployment: chatModel.name
  natPublicIp: natPublicIp.properties.ipAddress
  evidenceRetentionDays: evidenceRetentionDays
}

output staticWebAppUrl string = 'https://${staticWebApp.properties.defaultHostname}'
output foundryOpenAiBaseUrl string = 'https://${foundryAccountName}.openai.azure.com/openai/v1'
output chatModelDeploymentName string = chatModel.name

output identityIds object = {
  api: apiIdentity.id
  worker: workerIdentity.id
  imagePull: imagePullIdentity.id
}

output identityPrincipalIds object = {
  api: apiIdentity.properties.principalId
  worker: workerIdentity.properties.principalId
  imagePull: imagePullIdentity.properties.principalId
}

output identityClientIds object = {
  api: apiIdentity.properties.clientId
  worker: workerIdentity.properties.clientId
  imagePull: imagePullIdentity.properties.clientId
}
