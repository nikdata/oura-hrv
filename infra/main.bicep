// =====================================================================
// main.bicep — All Azure infrastructure for the Oura HRV pipeline
// =====================================================================
// This file defines every Azure resource needed to run the project.
// Deploy with:
//   az deployment group create --resource-group oura-hrv-rg --template-file infra/main.bicep
//
// After deployment, you still need to:
//   1. Set OURA_CLIENT_ID in the Function App settings (it's not in this template
//      because it varies per user and isn't sensitive enough for Key Vault)
//   2. Run scripts/oauth_bootstrap.py to seed tokens into Key Vault
//   3. Deploy the Function code: func azure functionapp publish ourahrv-func
//   4. Run scripts/create_subscription.py to register webhooks
// =====================================================================

@description('Base name for all resources (lowercase, no hyphens for storage)')
param baseName string = 'ourahrv'

@description('Azure region')
param location string = resourceGroup().location

// Resource names derived from the base name
var storageName = '${baseName}store'
var functionAppName = '${baseName}-func'
var keyVaultName = '${baseName}-kv'
var appInsightsName = '${baseName}-insights'
var hostingPlanName = '${baseName}-plan'
var containerName = 'oura-data'

// --- Storage Account ---
// One storage account serves double duty:
//   1. Azure Functions internal storage (AzureWebJobsStorage) — required
//      by the runtime for timer state, function coordination, etc.
//   2. Our data container (oura-data) — where HRV/RHR JSON files live.
// allowBlobPublicAccess=true is required because the Apple Shortcut reads
// blobs over plain HTTPS without authentication.
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

// The data container — public blob-level read access so the Apple
// Shortcut can fetch JSON files without auth tokens or SAS keys.
resource dataContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'Blob'
  }
}

// --- Application Insights (with daily cap) ---
// Logging and monitoring for the Function App. Logs from logger.info(),
// logger.warning(), etc. end up here. View in Azure Portal under the
// Function App's Monitor section.
// RetentionInDays=30 keeps costs low. The sampling settings in host.json
// further reduce ingestion volume.
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    RetentionInDays: 30
    IngestionMode: 'ApplicationInsights'
  }
}

// --- Key Vault ---
// Stores two secrets:
//   - oura-refresh-token: OAuth refresh token (rotates — Function writes back new values)
//   - oura-client-secret: Oura app client secret (also used for HMAC verification)
// enableRbacAuthorization=true means access is controlled via Azure role
// assignments (below), not the older "access policies" model.
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
  }
}

// --- Consumption Plan ---
// The cheapest Azure Functions hosting option. "Dynamic" = Consumption plan.
// Azure allocates compute on-demand when a function is triggered and
// deallocates when idle. You pay only for execution time.
// reserved=true indicates Linux (required for Python functions).
resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: hostingPlanName
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

// --- Function App ---
// The compute resource that runs our Python functions.
// identity.type='SystemAssigned' creates a managed identity — an Azure-internal
// "service account" that can authenticate to Key Vault and Blob Storage
// without any passwords in our code.
resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    siteConfig: {
      pythonVersion: '3.11'
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        // Required by Azure Functions runtime for internal bookkeeping
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storageName};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        // Tell Azure this is a Functions v4 app written in Python
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        // Link to Application Insights for logging
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsights.properties.InstrumentationKey }
        // Our app-specific settings (read by core/*.py at runtime)
        { name: 'KEY_VAULT_URI', value: keyVault.properties.vaultUri }
        { name: 'BLOB_ACCOUNT_URL', value: 'https://${storageName}.blob.core.windows.net' }
        { name: 'BLOB_CONTAINER_NAME', value: containerName }
        // Note: OURA_CLIENT_ID must be set manually after deployment:
        //   az functionapp config appsettings set --name ourahrv-func ...
      ]
    }
    httpsOnly: true
  }
}

// --- Key Vault RBAC: grant Function App "Key Vault Secrets Officer" ---
// This role allows both reading AND writing secrets. Writing is needed
// because the Oura refresh token rotates — when Oura returns a new
// refresh token, the Function writes it back to Key Vault.
// (A read-only role like "Key Vault Secrets User" would not be enough.)
resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: keyVault
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    // '4633458b-...' is the built-in role ID for "Key Vault Secrets Officer"
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

// --- Storage RBAC: grant Function App "Storage Blob Data Contributor" ---
// Allows the Function to upload (write) blobs to the oura-data container.
// The managed identity is used instead of storage account keys.
resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    // 'ba92f5b4-...' is the built-in role ID for "Storage Blob Data Contributor"
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}

// --- Outputs ---
// Printed after deployment. You'll need these values for setup steps.
output functionAppName string = functionApp.name
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'
output webhookUrl string = 'https://${functionApp.properties.defaultHostName}/api/webhook'
output blobBaseUrl string = 'https://${storageName}.blob.core.windows.net/${containerName}'
output keyVaultUri string = keyVault.properties.vaultUri
output storageAccountName string = storageName
