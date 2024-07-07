# AI_EMAIL: The Ai Agent parsing GMAIL emails

This is AI EMAIL, an AI agent capable of accessing your GMAIL account and read the emails for you.

You can choose topics or authors of interest, or topic/authors to discard and the agent will only retrieve the important news for you.

The Agent will provide you with a list of emails that it considers not of interest to the user, with a probability score. 
The unwanted emails will be set as "read" and move to trash.

For those of interest, instead, it provides the probability score and a summary of the email content.


## Setting up GMAIL:
Currently, you need access to  Google Cloud account at https://console.cloud.google.com/

1) Create your project 
2) Go to API and services
3) Enable GMAIl api from Library
4) Go to OAuth panel and set external or internal based on your choice. Add trial users in order to access your GMAIL from a Google Account.
5) Go to Credentials and click create new credentials. Once you create the credentials for OAuth authentication, download the json file to a local folder.
