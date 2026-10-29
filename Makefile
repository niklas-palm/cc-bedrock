.DEFAULT_GOAL := help
.PHONY: help deploy permission-set client outputs status logs test destroy

# Everything an organisation sets is in config.yaml (copy config.example.yaml). CONFIG=other.yaml for another.
export CONFIG ?= config.yaml
CDK    = npx --yes aws-cdk@2
CONFIG_VALUE = $(shell uv run python -c "import yaml; print(yaml.safe_load(open('$(CONFIG)'))$(1))")
NAME   = $(call CONFIG_VALUE,['name'])
REGION = $(call CONFIG_VALUE,['region'])
AWS    = aws --region $(REGION)

help:
	@echo "make deploy          deploy or update everything in the Bedrock account (safe to run again)"
	@echo "make permission-set  the Identity Center permission set, with its admin's credentials"
	@echo "make client          dist/: the installer and managed settings, for MDM or any other channel"
	@echo "make outputs         the dashboard URL and the redirect URI for your identity provider"
	@echo "make status          last telemetry and bill files, last budget check, who is blocked"
	@echo "make logs            follow the ingest function's logs (FN=ApiFunction or FN=Enforcer for the others)"
	@echo "make test            lint and unit tests, no AWS needed"
	@echo "make destroy         delete the stack (the data bucket is kept)"

deploy:
	@test -f $(CONFIG) || { echo "Copy config.example.yaml to $(CONFIG) and fill it in first."; exit 1; }
	$(CDK) bootstrap
	$(CDK) deploy --all --require-approval never
	uv run python infra/cur_export.py
	@$(MAKE) --no-print-directory outputs

permission-set:
	uv run python infra/permission_set.py

client:
	uv run python infra/make_client.py

outputs:
	@$(AWS) cloudformation describe-stacks --stack-name $(NAME) \
	  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table

status:
	@bucket=$$($(AWS) cloudformation describe-stacks --stack-name $(NAME) --output text \
	  --query 'Stacks[0].Outputs[?OutputKey==`DataBucket`].OutputValue'); \
	echo "Last telemetry file: $$($(AWS) s3 ls s3://$$bucket/telemetry/ --recursive | sort | tail -1 | cut -c1-19)"; \
	echo "Last bill file:      $$($(AWS) s3 ls s3://$$bucket/cur/ --recursive | sort | tail -1 | cut -c1-19)"; \
	echo "Telemetry Firehose couldn't convert: $$($(AWS) s3 ls s3://$$bucket/telemetry-errors/ --recursive | wc -l) files"; \
	$(AWS) s3 cp s3://$$bucket/state/budgets.json - 2>/dev/null | python3 -c "import json, sys; \
	  s = json.load(sys.stdin); print('Budgets checked:    ', s['updated']); \
	  print('Blocked:            ', ', '.join(s['blocked']) or 'nobody')" || echo "Budgets: not checked yet"

FN ?= Ingest
logs:
	$(AWS) logs tail $$($(AWS) logs describe-log-groups --log-group-name-prefix $(NAME)-$(FN)Logs \
	  --query 'logGroups[0].logGroupName' --output text) --follow

test:
	uvx ruff check .
	uv run python -m unittest discover -s tests -q

destroy:
	$(CDK) destroy --all --force
