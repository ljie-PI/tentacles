package com.lab_440.tentacles.master.scheduler;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

public interface IScheduler<E> {

    public void setDupChecker(IDupChecker<E> dupChecker);

    public boolean add(String domain, E item);

    public default int addBatch(String domain, List<E> items) {
        int n = 0;
        for (E item : items) {
            n += (add(domain, item) ? 1 : 0);
        }
        return n;
    }

    public E poll(String domain);

    public default List<E> pollBatch(String domain, int cnt) {
        List<E> retList = new ArrayList<>();
        for (int i = 0; i < cnt; i++) {
            E item = poll(domain);
            if (item != null)
                retList.add(item);
        }
        return retList;
    }

    public List<E> pollBatch(Set<String> exclude, int cnt);

}
