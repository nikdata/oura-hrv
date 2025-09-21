# Syncing Oura Ring's Sleep HRV to Apple Health

The Oura Ring Gen 4 is an excellent device to help track one's sleep. Interestingly, the Oura Ring (through the Oura app on iOS and Oura Cloud) does not directly sync HRV (Heart Rate Variability) to Apple Health.

> [!IMPORTANT]
> An active Oura subscription is required!

## Limitations

The type of HRV captured by the Oura ring is called RMSSD (Root Mean Square of Successive Differences) whereas the Apple Watch (and by extension, Apple Health) measure HRV as SDNN (Standard Deviation of NN intervals). Both of these values are *derived* values from the raw data that is collected on board the devices. Unfortunately, neither device exposes the actual raw data that is used to derive eitehr RMSSD or SDNN. Therefore, there is no accurate and straight-forward method to convert betwen the two values. However, using one or the other consistently will provide useful insights into HRV. Mixing the two can become misleading.

For more information, see [here](https://www.spikeapi.com/blog/understanding-hrv-metrics-a-deep-dive-into-sdnn-and-rmssd).

## The How

The code in this repo uses the Oura API v2 with OAuth and auto-refreshing tokens. It is deployed on GitHub and automatically (using a CRON schedule) runs. The results are saved in the ```data``` folder.

## Steps

1. Register application on Oura
2. Configure application on Oura
3. Configure the .env file
  - OURA_CLIENT_ID
  - OURA_CLIENT_SECRET
4. Run the script ```example/oura.py``` up to line 115
5. Click on the URL and authorize the app via Oura Webpage
6. In return URL, you will see a callback code:
https://example.com/callback?code=GBKPHXQTRUJW5NSDVKDCV72NV5WN2X2V&scope=personal%20daily%20heartrate
copy that code after ```code=```
7. Paste that code into line 125 where you see `authorization_code`
8. Run up to line 132 and you'll be asked to save the token values for the following keys:
  - OURA_ACCESS_TOKEN
  - OURA_REFRESH_TOKEN
9. Go to Repo Settings and then add the following key/values as your Repository secrets:
  - OURA_CLIENT_ID
  - OURA_CLIENT_SECRET
  - OURA_REFRESH_TOKEN
10. You'll want to create a PAT scoped to your specific repo. It will need FULL access
11. Save that PAT value as REPO_SECRETS_TOKEN in Repository Secrets

## Setup - Oura API

The best way to create a stable application using the Oura API v2 is to leverage OAuth. See their [documentation here](https://cloud.ouraring.com/v2/docs). Keep in mind that Oura does have Personal Access Tokens (PAT), but they are to be deprecated by the end of calendar year 2025.

When you go to register a new application to use the Oura API, you should see a page similar to this:

![](img/oura-api-app-setup-page.png)

You may enter *any* value(s) you'd like for the following fields:
- Display name
- Descripton
- Contact email
- Website
- Privacy Policy URL
- Terms of Service URL

For the Redirect URIs, I used https://example.com/callback

In my own debugging, this worked better than localhost.

I also clicked on "Allow server-side authentication". This ensures the tokens stay alive longer.

> [!NOTE]
> - [x] Allow server-side authentication (grant-type code)



