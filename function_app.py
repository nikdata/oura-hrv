# -----------------------------------------------------------------------
# function_app.py — Azure Functions entry point
# -----------------------------------------------------------------------
# This is the "main" file that Azure Functions looks for when your app
# starts. Think of it like the __init__.py of your entire Function App.
#
# Azure Functions v2 (Python) uses a "Blueprint" pattern:
#   - Each function (webhook_receiver, renew_subscriptions) is defined
#     in its own file under functions/ using a Blueprint.
#   - This file creates the FunctionApp and registers those Blueprints.
#
# Azure will discover and run any functions registered here. You don't
# need to touch this file unless you add a brand new function.
# -----------------------------------------------------------------------

import azure.functions as func

from functions.webhook_receiver import bp as webhook_bp
from functions.renew_subscriptions import bp as renew_bp

# Create the app and register both function blueprints
app = func.FunctionApp()
app.register_functions(webhook_bp)
app.register_functions(renew_bp)
