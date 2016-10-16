package com.lab_440.tentacles.master.handlers;

import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.scheduler.IScheduler;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.item.IItem;
import io.vertx.core.Handler;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.ext.web.RoutingContext;

public class ReplyHandler implements Handler<RoutingContext> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private IScheduler<IItem> scheduler;

    public ReplyHandler(IScheduler<IItem> scheduler) {
        this.scheduler = scheduler;
    }

    @Override
    public void handle(RoutingContext ctx) {
        String resp = "OK";
        if (scheduler != null) {
            JsonObject jobj = ctx.getBodyAsJson();
            String status = jobj.getString("status",
                    ProcessStatus.NOT_RETURN.toString());
            String url = jobj.getString("url", "");
            if (status.equals(ProcessStatus.NOT_RETURN.toString())) {
                int retried = scheduler.retry(new RequestItem().fromJsonObject(jobj));
                if (retried > 0) {
                    resp = "Failed to fetch " + url + " after " + retried + " retries";
                } else {
                    resp = "Failed to fetch " + url + ", will retry";
                }
                logger.error(resp);
            } else if (status.equals(ProcessStatus.BLOCKED.toString())) {
                resp = "Request to " + url + " is BLOCKED";
                logger.error(resp);
            } else if (status.equals(ProcessStatus.TMPL_CHANGED.toString())) {
                resp = "Template of " + url + " is CHANGED";
                logger.error(resp);
            }
        }
        ctx.response().end(resp);
    }

}
