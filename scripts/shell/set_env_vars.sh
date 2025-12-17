# Takes 1 argument: the path to a JSON file that specifies the (key, value) pairs to
# export as environment variables.
# Usage: source set_env_vars.sh path/to/env_vars.json
# NB: This script requires JQ.
for s in $(jq -r "to_entries|map(\"\(.key)=\(.value|tostring)\")|.[]" $1); do
    export $s
done
