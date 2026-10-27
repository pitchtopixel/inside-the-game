// Inside the Game - Azure infrastructure as code.
// Deploys into the resource group that already holds the Foundry account and its Application Insights:
//   Container Apps environment + app (managed identity, HTTP autoscale, health probes), "Foundry User" role,
//   e-mail action group and the model fallback-rate alert.
// The deploy REPLACES the registry secret: test the token first, or the new revision cannot pull the image (site down):
//   curl.exe -s -u "pitchtopixel:$t" "https://ghcr.io/token?scope=repository:pitchtopixel/inside-the-game:pull"  -> {"token":...}
// Check first:  az deployment group what-if -g <rg> -f infra/main.bicep -p infra/main.parameters.json -p registryPassword=... alertEmail=...
// Deploy:       az deployment group create  -g <rg> -f infra/main.bicep -p infra/main.parameters.json -p registryPassword=... alertEmail=...

@description('Region of the app (same as the Foundry project)')
param location string = resourceGroup().location

@description('Container image to run')
param image string = 'ghcr.io/pitchtopixel/inside-the-game:v10'

@description('Container registry and its read-only user')
param registryServer string = 'ghcr.io'
param registryUsername string = 'pitchtopixel'

@secure()
@description('Registry read token (classic GitHub token with read:packages) - passed at deploy time, never stored')
param registryPassword string

@description('Existing Foundry (AI Services) account and project')
param foundryAccountName string
param foundryProjectName string = 'inside-the-game'

@description('Model deployment the agents use')
param foundryModel string = 'gpt-4.1-mini'

@description('Existing Application Insights linked to the Foundry project (traces + fallback logs)')
param appInsightsName string

@description('E-mail for the fallback-rate alert - passed at deploy time, not stored in the repo')
param alertEmail string

@description('Name of the existing "Foundry User" role assignment (keeps the current one instead of adding a duplicate)')
param foundryRoleAssignmentName string = ''

@description('Scale: replicas and when to add one (each replica serves up to 5 viewers; scale out before it is full)')
param minReplicas int = 1
param maxReplicas int = 3
param concurrentRequestsPerReplica int = 4

var appName = 'inside-the-game'
var envName = 'itg-env'
var port = 8600
var foundryUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d' // built-in "Foundry User" (formerly "Azure AI User")

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// No Log Analytics: no log charges (traces go to Application Insights from the app itself)
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {}
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned' // signs in to Foundry - no API keys
  }
  properties: {
    managedEnvironmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: port
        transport: 'auto'
      }
      registries: [
        {
          server: registryServer
          username: registryUsername
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: [
        {
          name: 'registry-password'
          value: registryPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'LLM_PROVIDER', value: 'foundry' }
            { name: 'FOUNDRY_MODEL', value: foundryModel }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}' }
            { name: 'TRACING', value: '1' }
          ]
          probes: [
            {
              // restart the replica if the web server stops answering
              type: 'Liveness'
              httpGet: { path: '/', port: port }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              // send no viewers to a replica until it serves the page (answer cache loaded)
              type: 'Readiness'
              httpGet: { path: '/', port: port }
              initialDelaySeconds: 3
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            // a viewer holds one WebSocket: scale out at 4 per replica, before the 5-viewer cap answers "busy"
            name: 'viewers'
            http: {
              metadata: {
                concurrentRequests: string(concurrentRequestsPerReplica)
              }
            }
          }
        ]
      }
    }
  }
}

resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: empty(foundryRoleAssignmentName) ? guid(foundryAccount.id, appName, foundryUserRoleId) : foundryRoleAssignmentName
  scope: foundryAccount
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'itg-alerts'
  location: 'global'
  properties: {
    groupShortName: 'itgalerts'
    enabled: true
    emailReceivers: [
      {
        name: 'owner'
        emailAddress: alertEmail
        useCommonAlertSchema: false
      }
    ]
  }
}

// Quality alert: more than 10% of model answers replaced by templates in 15 min (slow or failing model)
resource fallbackAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'itg-llm-fallback-rate'
  location: location
  properties: {
    description: 'More than 10% of model answers replaced by templates in 15 min (slow/failing model)'
    severity: 2
    enabled: true
    scopes: [ appInsights.id ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: 'traces | where message in (\'llm_answer\',\'llm_fallback\') | summarize calls=count(), fallbacks=countif(message==\'llm_fallback\') | where calls >= 10 | extend fallback_pct=100.0*fallbacks/calls'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'fallback_pct'
          operator: 'GreaterThan'
          threshold: 10
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [ actionGroup.id ]
    }
  }
}

output url string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output principalId string = containerApp.identity.principalId
