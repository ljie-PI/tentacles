package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.item.IItem;

import java.lang.reflect.Constructor;

public class SchedulerHelper {

    private volatile static IScheduler<IItem> instance;

    public static void createInstance(Configuration conf) throws Exception {
        if (instance == null) {
            synchronized (SchedulerHelper.class) {
                if (instance == null) {
                    Class<?> schedulerClass = Class.forName(conf.getSchedulerClass());
                    Class<?> dupCheckerClass = Class.forName(conf.getDupCheckerClass());
                    Constructor schedulerConstructor = schedulerClass.getConstructor(Configuration.class);
                    instance = (IScheduler<IItem>) schedulerConstructor.newInstance(conf);
                    Constructor dupCheckerConstructor = dupCheckerClass.getConstructor(Configuration.class);
                    IDupChecker<IItem> dupChecker = (IDupChecker<IItem>) dupCheckerConstructor.newInstance(conf);
                    instance.setDupChecker(dupChecker);
                }
            }
        }
    }

    public static IScheduler<IItem> getInstance() {
        return instance;
    }
}
