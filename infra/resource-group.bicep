targetScope = 'subscription'

@minLength(1)
@maxLength(90)
@description('Name of the new FinGPT resource group.')
param resourceGroupName string

@description('Azure region for the resource group and workload.')
param location string

@description('Owner tag value.')
param owner string

@allowed([
  'dev'
  'test'
  'prod'
])
param environmentName string = 'dev'

resource finGptResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Project: 'Where my bucks go?'
    Environment: environmentName
    Owner: owner
    DataClassification: 'Confidential'
  }
}

output resourceGroupName string = finGptResourceGroup.name
output resourceGroupId string = finGptResourceGroup.id
