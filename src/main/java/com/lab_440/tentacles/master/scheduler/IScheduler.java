package com.lab_440.tentacles.master.scheduler;

import java.util.ArrayList;
import java.util.List;

public interface IScheduler<E> {

    public void setDupChecker(IDupChecker<E> dupChecker);

    public boolean add(E item);

    default public int addBatch(List<E> items) {
        int n = 0;
        for (E item: items) {
            n += (add(item) ? 1 : 0);
        }
        return n;
    }

    public E poll();

    default public List<E> pollBatch(int cnt) {
        List<E> retList = new ArrayList<E>();
        for (int i = 0; i < cnt; i++) {
            E item = poll();
            if (item != null)
                retList.add(item);
        }
        return retList;
    }

    public int retry(E item);

}
