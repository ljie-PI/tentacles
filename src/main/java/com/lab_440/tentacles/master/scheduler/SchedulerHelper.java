package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.item.AbstractItem;

import java.lang.reflect.Constructor;

public class SchedulerHelper {

    private volatile static IScheduler<AbstractItem> instance;

    public static void createInstance(Configuration conf) throws Exception {
        if (instance == null) {
            synchronized (SchedulerHelper.class) {
                if (instance == null) {
                    Class<?> schedulerClass = Class.forName(conf.getSchedulerClass());
                    Class<?> dupCheckerClass = Class.forName(conf.getDupCheckerClass());
                    Constructor schedulerConstructor = schedulerClass.getConstructor(Configuration.class);
                    instance = (IScheduler<AbstractItem>) schedulerConstructor.newInstance(conf);
                    Constructor dupCheckerConstructor = dupCheckerClass.getConstructor(Configuration.class);
                    IDupChecker<AbstractItem> dupChecker = (IDupChecker<AbstractItem>) dupCheckerConstructor.newInstance(conf);
                    instance.setDupChecker(dupChecker);
                }
            }
        }
    }

    public static IScheduler<AbstractItem> getInstance() {
        return instance;
    }
}
