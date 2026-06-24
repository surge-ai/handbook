FROM handbook_base

COPY initial_external_services/ /data/
COPY initial_external_services/ /initial_data/
COPY initial_workspace/ /workdir/

ENV INPUTDIR=/data OUTPUTDIR=/data WORLDBENCH_TOOL_SETS="syntara_ds_all google_mail_contacts slack_core google_mail_core google_calendar_core jira_core shopify_core"
