package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.common.IDGenerator;
import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.item.IItem;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.HashSet;

public class SetDupChecker implements IDupChecker<IItem> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private HashSet<String> itemSet;

    public SetDupChecker(Configuration conf) {
        itemSet = new HashSet<>();
    }

    @Override
    public boolean isDuplicated(IItem item) {
        String id = IDGenerator.generateID(item.identity());
        return !itemSet.add(id);
    }

}
