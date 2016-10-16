package com.lab_440.tentacles.master.handlers;

import com.lab_440.tentacles.common.item.IItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.scheduler.IScheduler;
import io.vertx.core.Handler;
import io.vertx.core.json.JsonArray;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.ext.web.RoutingContext;

public class FollowLinksHandler implements Handler<RoutingContext> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private IScheduler<IItem> scheduler;

    public FollowLinksHandler(IScheduler<IItem> scheduler) {
        this.scheduler = scheduler;
    }

    @Override
    public void handle(RoutingContext ctx) {
        int added = 0;
        if (scheduler != null) {
            JsonArray jarr = ctx.getBodyAsJsonArray();
            for (int i = 0; i < jarr.size(); i++) {
                IItem item = new RequestItem()
                        .fromJsonObject(jarr.getJsonObject(i));
                if (scheduler.add(item)) {
                    added++;
                }
            }
        }
        ctx.response().end(String.valueOf(added));
    }
}
