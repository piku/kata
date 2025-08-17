export PYTHONIOENCODING=UTF_8:replace
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export CURRENT_GIT_BRANCH?=`git symbolic-ref --short HEAD`

.DEFAULT_GOAL := help

test: ## Run local test
	python kata.py 

deploy-paas: ## Push to test
	# POST entire kata.py to home server on port 8000
	curl -X POST --data-binary @kata.py -H "Content-Type: text/plain" http://paas:8000

deploy-home: ## Push to test
	# POST entire kata.py to home server on port 8000
	curl -X POST --data-binary @kata.py -H "Content-Type: text/plain" http://home.local:8000

help:
	@grep -hE '^[A-Za-z0-9_ \-]*?:.*##.*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
