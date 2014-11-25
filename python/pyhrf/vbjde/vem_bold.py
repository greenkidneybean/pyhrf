# -*- coding: utf-8 -*-
"""VEM BOLD Constrained

Files that contain functions for BOLD data analysis

Different implementations (1) with C extensions, (2) all in python,
(3) to check differences

WARNING: NOT WORKING!!
"""

import os.path as op
import numpy as np
import time
import UtilsC
import pyhrf
from pyhrf.tools._io import read_volume 
from pyhrf.boldsynth.hrf import getCanoHRF
from pyhrf.ndarray import xndarray
import vem_tools as vt
try:
    from collections import OrderedDict
except ImportError:
    from pyhrf.tools.backports import OrderedDict


def Main_vbjde_Extension(graph,Y,Onsets,Thrf,K,TR,beta,dt,scale=1,estimateSigmaH=True,sigmaH = 0.05,NitMax = -1,NitMin = 1,estimateBeta=True,PLOT=False,contrasts=[],computeContrast=False,gamma_h=0,estimateHRF=True,TrueHrfFlag=False,HrfFilename='hrf.nii',estimateLabels=True,LabelsFilename='labels.nii',MFapprox=False,InitVar=0.5,InitMean=2.0,MiniVEMFlag=False,NbItMiniVem=5):    
    # VEM BOLD classic, using extension in C
    
    pyhrf.verbose(1,"Fast EM with C extension started ...")
    np.random.seed(6537546)

    tau1 = 0.0
    tau2 = 0.0
    S = 100
    Init_sigmaH = sigmaH

    Nb2Norm = 1
    NormFlag = False    
    
    if NitMax < 0:
        NitMax = 100
    gamma = 7.5#7.5
    #gamma_h = 1000
    gradientStep = 0.003
    MaxItGrad = 200
    Thresh = 1e-5
    Thresh_FreeEnergy = 1e-5
    
    # Initialize sizes vectors
    #D = int(np.ceil(Thrf/dt)) ##############################
    D = int(np.ceil(Thrf/dt)) + 1
    M = len(Onsets)
    N = Y.shape[0]
    J = Y.shape[1]
    l = int(np.sqrt(J))
    condition_names = []

    maxNeighbours = max([len(nl) for nl in graph])
    neighboursIndexes = np.zeros((J, maxNeighbours), dtype=np.int32)
    neighboursIndexes -= 1
    for i in xrange(J):
        neighboursIndexes[i,:len(graph[i])] = graph[i]
    #-----------------------------------------------------------------------#
    
    X = OrderedDict([])
    for condition,Ons in Onsets.iteritems():
        X[condition] = vt.compute_mat_X_2(N, TR, D, dt, Ons)
        condition_names += [condition]
    XX = np.zeros((M,N,D),dtype=np.int32)
    nc = 0
    for condition,Ons in Onsets.iteritems():
        XX[nc,:,:] = X[condition]
        nc += 1
        
    order = 2
    D2 = vt.buildFiniteDiffMatrix(order,D)
    R = np.dot(D2,D2) / pow(dt,2*order)
    invR = np.linalg.inv(R)
    Det_invR = np.linalg.det(invR)
    
    Gamma = np.identity(N)
    Det_Gamma = np.linalg.det(Gamma)

    p_Wtilde = np.zeros((M,K),dtype=np.float64)
    p_Wtilde1 = np.zeros((M,K),dtype=np.float64)
    p_Wtilde[:,1] = 1

    Crit_H = 1
    Crit_Z = 1
    Crit_A = 1
    Crit_AH = 1
    AH = np.zeros((J,M,D),dtype=np.float64)
    AH1 = np.zeros((J,M,D),dtype=np.float64)
    Crit_FreeEnergy = 1
    
    cA = []
    cH = []
    cZ = []
    cAH = []
    FreeEnergy_Iter = []
    cTime = []
    cFE = []
    
    SUM_q_Z = [[] for m in xrange(M)]
    mu1 = [[] for m in xrange(M)]
    h_norm = []
    
    CONTRAST = np.zeros((J,len(contrasts)),dtype=np.float64)
    CONTRASTVAR = np.zeros((J,len(contrasts)),dtype=np.float64)
    Q_barnCond = np.zeros((M,M,D,D),dtype=np.float64)
    XGamma = np.zeros((M,D,N),dtype=np.float64)
    m1 = 0
    for k1 in X: # Loop over the M conditions
        m2 = 0
        for k2 in X:
            Q_barnCond[m1,m2,:,:] = np.dot(np.dot(X[k1].transpose(),Gamma),X[k2])
            m2 += 1
        XGamma[m1,:,:] = np.dot(X[k1].transpose(),Gamma)
        m1 += 1
    
    if MiniVEMFlag: 
        pyhrf.verbose(1,"MiniVEM to choose the best initialisation...")
        InitVar, InitMean, gamma_h = vt.MiniVEM_CompMod(Thrf,TR,dt,beta,Y,K,gamma,gradientStep,MaxItGrad,D,M,N,J,S,maxNeighbours,neighboursIndexes,XX,X,R,Det_invR,Gamma,Det_Gamma,p_Wtilde,scale,Q_barnCond,XGamma,tau1,tau2,NbItMiniVem,sigmaH,estimateHRF)

    sigmaH = Init_sigmaH
    sigma_epsilone = np.ones(J)
    if 0:
        pyhrf.verbose(3,"Labels are initialized by setting active probabilities to zeros ...")
        q_Z = np.ones((M,K,J),dtype=np.float64)
        q_Z[:,1,:] = 0
    if 0:
        pyhrf.verbose(3,"Labels are initialized randomly ...")
        q_Z = np.zeros((M,K,J),dtype=np.float64)
        nbVoxInClass = J/K
        for j in xrange(M) :
            if J%2==0:
                l = []
            else:
                l = [0]
            for c in xrange(K) :
                l += [c] * nbVoxInClass
            q_Z[j,0,:] = np.random.permutation(l)
            q_Z[j,1,:] = 1. - q_Z[j,0,:]
    if 1:
        pyhrf.verbose(3,"Labels are initialized by setting active probabilities to ones ...")
        q_Z = np.zeros((M,K,J),dtype=np.float64)
        q_Z[:,1,:] = 1
        
    q_Z1 = np.zeros((M,K,J),dtype=np.float64)   
    Z_tilde = q_Z.copy()
    
    #TT,m_h = getCanoHRF(Thrf-dt,dt) #TODO: check
    TT,m_h = getCanoHRF(Thrf,dt) #TODO: check
    m_h = m_h[:D]
    m_H = np.array(m_h).astype(np.float64)
    m_H1 = np.array(m_h)
    sigmaH1 = sigmaH
    if estimateHRF:
        Sigma_H = np.ones((D,D),dtype=np.float64)
    else:
        Sigma_H = np.zeros((D,D),dtype=np.float64)
    
    Beta = beta * np.ones((M),dtype=np.float64)
    P = vt.PolyMat( N , 4 , TR)
    L = vt.polyFit(Y, TR, 4,P)
    PL = np.dot(P,L)
    y_tilde = Y - PL
    Ndrift = L.shape[0]

    sigma_M = np.ones((M,K),dtype=np.float64)
    sigma_M[:,0] = 0.5
    sigma_M[:,1] = 0.6
    mu_M = np.zeros((M,K),dtype=np.float64)
    for k in xrange(1,K):
        mu_M[:,k] = InitMean
    Sigma_A = np.zeros((M,M,J),np.float64)
    for j in xrange(0,J):
        Sigma_A[:,:,j] = 0.01*np.identity(M)    
    m_A = np.zeros((J,M),dtype=np.float64)
    m_A1 = np.zeros((J,M),dtype=np.float64)    
    for j in xrange(0,J):
        for m in xrange(0,M):
            for k in xrange(0,K):
                m_A[j,m] += np.random.normal(mu_M[m,k], np.sqrt(sigma_M[m,k]))*q_Z[m,k,j]
    m_A1 = m_A        
            
    t1 = time.time()
    
    for ni in xrange(0,NitMin):
        pyhrf.verbose(1,"------------------------------ Iteration n° " + str(ni+1) + " ------------------------------")
        pyhrf.verbose(3, "E A step ...")
        #t01 = time.time()
        UtilsC.expectation_A(q_Z,mu_M,sigma_M,PL,sigma_epsilone,Gamma,Sigma_H,Y,y_tilde,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N,K)
        
        val = np.reshape(m_A,(M*J))
        val[ np.where((val<=1e-50) & (val>0.0)) ] = 0.0
        val[ np.where((val>=-1e-50) & (val<0.0)) ] = 0.0
        #m_A = np.reshape(val, (J,M))
        
        if estimateHRF:
            UtilsC.expectation_H(XGamma,Q_barnCond,sigma_epsilone,Gamma,R,Sigma_H,Y,y_tilde,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N,scale,sigmaH)
            m_H[0] = 0
            m_H[-1] = 0
            h_norm += [np.linalg.norm(m_H)]
            # Normalizing H at each Nb2Norm iterations:
            if NormFlag:
                # Normalizing is done before sigmaH, mu_M and sigma_M estimation
                # we should not include them in the normalisation step
                if (ni+1)%Nb2Norm == 0:
                    Norm = np.linalg.norm(m_H)
                    m_H /= Norm
                    Sigma_H /= Norm**2
                    m_A *= Norm
                    Sigma_A *= Norm**2
            # Plotting HRF
            if PLOT and ni >= 0:
                import matplotlib.pyplot as plt
                plt.figure(M+1)
                plt.plot(m_H)
                plt.hold(True)
        else:
            if TrueHrfFlag:
                #TrueVal, head = read_volume(HrfFilename)
                TrueVal, head = read_volume(HrfFilename)[:,0,0,0]
                print TrueVal
                print TrueVal.shape
                m_H = TrueVal
                
        DIFF = np.reshape( m_A - m_A1,(M*J) )
        DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
        DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
        Crit_A = (np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(m_A1,(M*J)) ))**2
        cA += [Crit_A]
        m_A1[:,:] = m_A[:,:]
        
        Crit_H = (np.linalg.norm( m_H - m_H1 ) / np.linalg.norm( m_H1 ))**2
        cH += [Crit_H]
        m_H1[:] = m_H[:]

        for d in xrange(0,D):
            AH[:,:,d] = m_A[:,:]*m_H[d]
        DIFF = np.reshape( AH - AH1,(M*J*D) )
        DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
        DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
        Crit_AH = (np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(AH1,(M*J*D)) ))**2
        cAH += [Crit_AH]
        AH1[:,:,:] = AH[:,:,:]
        
        if estimateLabels:
            pyhrf.verbose(3, "E Z step ...")
            if MFapprox:
                UtilsC.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
            if not MFapprox:
                UtilsC.expectation_Z_ParsiMod_RVM_and_CompMod(Sigma_A,m_A,sigma_M,Beta,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
                #UtilsC.expectation_Z_ParsiMod_3(Sigma_A,m_A,sigma_M,Beta,p_Wtilde,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
        else:
            pyhrf.verbose(3, "Using True Z ...")
            TrueZ = read_volume(LabelsFilename)
            for m in xrange(M):
                q_Z[m,1,:] = np.reshape(TrueZ[0][:,:,:,m],J)
                q_Z[m,0,:] = 1 - q_Z[m,1,:]            
        
        val = np.reshape(q_Z,(M*K*J))
        val[ np.where((val<=1e-50) & (val>0.0)) ] = 0.0
        #q_Z = np.reshape(val, (M,K,J))
        
        DIFF = np.reshape( q_Z - q_Z1,(M*K*J) )
        DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
        DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
        Crit_Z = ( np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(q_Z1,(M*K*J)) ))**2
        cZ += [Crit_Z]
        q_Z1[:,:,:] = q_Z[:,:,:]
        
        #DIFF = abs(np.reshape(q_Z,(M*K*J)) - np.reshape(q_Z1,(M*K*J)))
        #DIFF[ find( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
        #Crit_Z = (sum(DIFF) / len(find(DIFF != 0)))**2
        #cZ += [Crit_Z]
        #q_Z1[:,:,:] = q_Z[:,:,:]
        
        if estimateHRF:
            if estimateSigmaH:
                pyhrf.verbose(3,"M sigma_H step ...")
                if gamma_h > 0:
                    sigmaH = vt.maximization_sigmaH_prior(D,Sigma_H,R,m_H,gamma_h)
                else:
                    sigmaH = vt.maximization_sigmaH(D,Sigma_H,R,m_H)
                pyhrf.verbose(3,'sigmaH = ' + str(sigmaH))
        
        pyhrf.verbose(3,"M (mu,sigma) step ...")
        mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M,Sigma_A)
        
        for m in xrange(M):
            SUM_q_Z[m] += [sum(q_Z[m,1,:])]
            mu1[m] += [mu_M[m,1]]
        
        UtilsC.maximization_L(Y,m_A,m_H,L,P,XX.astype(np.int32),J,D,M,Ndrift,N)
        
        PL = np.dot(P,L)
        y_tilde = Y - PL
        if estimateBeta:
            pyhrf.verbose(3,"estimating beta")
            for m in xrange(0,M):
                if MFapprox:
                    Beta[m] = UtilsC.maximization_beta(beta,q_Z[m,:,:].astype(np.float64),Z_tilde[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
                if not MFapprox:
                    #Beta[m] = UtilsC.maximization_beta(beta,q_Z[m,:,:].astype(np.float64),q_Z[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
                    Beta[m] = UtilsC.maximization_beta_CB(beta,q_Z[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
            pyhrf.verbose(3,"End estimating beta")
            pyhrf.verbose.printNdarray(3, Beta)
        pyhrf.verbose(3,"M sigma noise step ...")
        UtilsC.maximization_sigma_noise(Gamma,PL,sigma_epsilone,Sigma_H,Y,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N)
        
        #### Computing Free Energy ####
        if ni > 0:
            FreeEnergy1 = FreeEnergy
        FreeEnergy = vt.Compute_FreeEnergy(y_tilde,m_A,Sigma_A,mu_M,sigma_M,m_H,Sigma_H,R,Det_invR,sigmaH,p_Wtilde,tau1,tau2,q_Z,neighboursIndexes,maxNeighbours,Beta,sigma_epsilone,XX,Gamma,Det_Gamma,XGamma,J,D,M,N,K,S,"CompMod")
        if ni > 0:
            Crit_FreeEnergy = (FreeEnergy1 - FreeEnergy) / FreeEnergy1
        FreeEnergy_Iter += [FreeEnergy]
        cFE += [Crit_FreeEnergy]
        
        t02 = time.time()
        cTime += [t02-t1]
        
        #print 'sigma_noise =',sigma_epsilone
        #t02 = time.time()
        #cTime += [t02-t1]
    #m_H1[:] = m_H[:]
    #q_Z1[:,:,:] = q_Z[:,:,:]
    #m_A1[:,:] = m_A[:,:]

    pyhrf.verbose(2,"------------------------------ Iteration n° " + str(ni+2) + " ------------------------------")
    UtilsC.expectation_A(q_Z,mu_M,sigma_M,PL,sigma_epsilone,Gamma,Sigma_H,Y,y_tilde,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N,K)

    val = np.reshape(m_A,(M*J))
    val[ np.where((val<=1e-50) & (val>0.0)) ] = 0.0
    val[ np.where((val>=-1e-50) & (val<0.0)) ] = 0.0
    #m_A = np.reshape(val, (J,M))

    if estimateHRF:
      UtilsC.expectation_H(XGamma,Q_barnCond,sigma_epsilone,Gamma,R,Sigma_H,Y,y_tilde,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N,scale,sigmaH)
      m_H[0] = 0
      m_H[-1] = 0
      h_norm += [np.linalg.norm(m_H)]
      # Normalizing H at each Nb2Norm iterations:
      if NormFlag:
          if (ni+2)%Nb2Norm == 0:
              Norm = np.linalg.norm(m_H)
              m_H /= Norm
              Sigma_H /= Norm**2
              m_A *= Norm
              Sigma_A *= Norm**2
      # Plotting HRF        
      if PLOT and ni >= 0:
          import matplotlib.pyplot as plt
          plt.figure(M+1)
          plt.plot(m_H)
          plt.hold(True)
    
    else:
        if TrueHrfFlag:
            TrueVal, head = read_volume(HrfFilename)[:,0,0,0]
            m_H = TrueVal
    
    #DIFF = abs(np.reshape(m_A,(M*J)) - np.reshape(m_A1,(M*J)))
    #Crit_A = sum(DIFF) / len(find(DIFF != 0))
    DIFF = np.reshape( m_A - m_A1,(M*J) )
    DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
    DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
    Crit_A = (np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(m_A1,(M*J)) ))**2
    cA += [Crit_A]
    m_A1[:,:] = m_A[:,:]    
        
    Crit_H = (np.linalg.norm( m_H - m_H1 ) / np.linalg.norm( m_H1 ))**2
    #Crit_H = abs(np.mean(m_H - m_H1) / np.mean(m_H))
    cH += [Crit_H]
    m_H1[:] = m_H[:]

    for d in xrange(0,D):
        AH[:,:,d] = m_A[:,:]*m_H[d]
    DIFF = np.reshape( AH - AH1,(M*J*D) )
    DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
    DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
    Crit_AH = (np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(AH1,(M*J*D)) ))**2
    cAH += [Crit_AH]
    AH1[:,:,:] = AH[:,:,:]
    
    if estimateLabels:
        if MFapprox:
            UtilsC.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
        if not MFapprox:
            UtilsC.expectation_Z_ParsiMod_RVM_and_CompMod(Sigma_A,m_A,sigma_M,Beta,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
    else:
        pyhrf.verbose(3, "Using True Z ...")
        TrueZ = read_volume(LabelsFilename)
        for m in xrange(M):
            q_Z[m,1,:] = np.reshape(TrueZ[0][:,:,:,m],J)
            q_Z[m,0,:] = 1 - q_Z[m,1,:]
    
    val = np.reshape(q_Z,(M*K*J))
    val[ np.where((val<=1e-50) & (val>0.0)) ] = 0.0
    #q_Z = np.reshape(val, (M,K,J))
    
    DIFF = np.reshape( q_Z - q_Z1,(M*K*J) )
    DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
    DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
    Crit_Z = ( np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(q_Z1,(M*K*J)) ))**2
    cZ += [Crit_Z]
    q_Z1[:,:,:] = q_Z[:,:,:]
    
    #DIFF = abs(np.reshape(q_Z,(M*K*J)) - np.reshape(q_Z1,(M*K*J)))
    #DIFF[ find( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
    #Crit_Z = (sum(DIFF) / len(find(DIFF != 0)))**2
    #cZ += [Crit_Z]
    #q_Z1[:,:,:] = q_Z[:,:,:]
    
    if estimateHRF:
        if estimateSigmaH:
            pyhrf.verbose(3,"M sigma_H step ...")
            if gamma_h > 0:
                sigmaH = vt.maximization_sigmaH_prior(D,Sigma_H,R,m_H,gamma_h)
            else:
                sigmaH = vt.maximization_sigmaH(D,Sigma_H,R,m_H)
            pyhrf.verbose(3,'sigmaH = ' + str(sigmaH))
            
    mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M,Sigma_A)

    for m in xrange(M):
        SUM_q_Z[m] += [sum(q_Z[m,1,:])]
        mu1[m] += [mu_M[m,1]]
        
    UtilsC.maximization_L(Y,m_A,m_H,L,P,XX.astype(np.int32),J,D,M,Ndrift,N)
    PL = np.dot(P,L)
    y_tilde = Y - PL
    if estimateBeta:
        pyhrf.verbose(3,"estimating beta")
        for m in xrange(0,M):
            if MFapprox:
                Beta[m] = UtilsC.maximization_beta(beta,q_Z[m,:,:].astype(np.float64),Z_tilde[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
            if not MFapprox:    
                #Beta[m] = UtilsC.maximization_beta(beta,q_Z[m,:,:].astype(np.float64),q_Z[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
                Beta[m] = UtilsC.maximization_beta_CB(beta,q_Z[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
        pyhrf.verbose(3,"End estimating beta")
        pyhrf.verbose.printNdarray(3, Beta)
    UtilsC.maximization_sigma_noise(Gamma,PL,sigma_epsilone,Sigma_H,Y,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N)
    
    #### Computing Free Energy ####
    FreeEnergy1 = FreeEnergy
    FreeEnergy = vt.Compute_FreeEnergy(y_tilde,m_A,Sigma_A,mu_M,sigma_M,m_H,Sigma_H,R,Det_invR,sigmaH,p_Wtilde,tau1,tau2,q_Z,neighboursIndexes,maxNeighbours,Beta,sigma_epsilone,XX,Gamma,Det_Gamma,XGamma,J,D,M,N,K,S,"CompMod")
    Crit_FreeEnergy = (FreeEnergy1 - FreeEnergy) / FreeEnergy1
    FreeEnergy_Iter += [FreeEnergy]
    cFE += [Crit_FreeEnergy]

    t02 = time.time()
    cTime += [t02-t1]
    ni += 2

    
    if ((Crit_FreeEnergy > Thresh_FreeEnergy) or (Crit_AH > Thresh)):
        while ( ((Crit_FreeEnergy > Thresh_FreeEnergy) or (Crit_AH > Thresh)) and (ni < NitMax) ):
            pyhrf.verbose(1,"------------------------------ Iteration n° " + str(ni+1) + " ------------------------------")
            #t01 = time.time()
            UtilsC.expectation_A(q_Z,mu_M,sigma_M,PL,sigma_epsilone,Gamma,Sigma_H,Y,y_tilde,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N,K)
            
            val = np.reshape(m_A,(M*J))
            val[ np.where((val<=1e-50) & (val>0.0)) ] = 0.0
            val[ np.where((val>=-1e-50) & (val<0.0)) ] = 0.0
            #m_A = np.reshape(val, (J,M))
            
            if estimateHRF:
                UtilsC.expectation_H(XGamma,Q_barnCond,sigma_epsilone,Gamma,R,Sigma_H,Y,y_tilde,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N,scale,sigmaH)
                m_H[0] = 0
                m_H[-1] = 0
                h_norm += [np.linalg.norm(m_H)]
                if NormFlag:
                    if (ni+1)%Nb2Norm == 0:
                        Norm = np.linalg.norm(m_H)
                        m_H /= Norm
                        Sigma_H /= Norm**2
                        m_A *= Norm
                        Sigma_A *= Norm**2
                # Plotting HRF        
                if PLOT and ni >= 0:
                    import matplotlib.pyplot as plt
                    plt.figure(M+1)
                    plt.plot(m_H)
                    plt.hold(True)
            
            else:
                if TrueHrfFlag:
                    TrueVal, head = read_volume(HrfFilename)[:,0,0,0]
                    m_H = TrueVal
            
            #DIFF = abs(np.reshape(m_A,(M*J)) - np.reshape(m_A1,(M*J)))
            #Crit_A = sum(DIFF) / len(find(DIFF != 0))
            DIFF = np.reshape( m_A - m_A1,(M*J) )
            DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
            DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
            Crit_A = (np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(m_A1,(M*J)) ))**2
            m_A1[:,:] = m_A[:,:]
            cA += [Crit_A]       
                    
            Crit_H = (np.linalg.norm( m_H - m_H1 ) / np.linalg.norm( m_H1 ))**2
            #Crit_H = abs(np.mean(m_H - m_H1) / np.mean(m_H))
            cH += [Crit_H]
            m_H1[:] = m_H[:]

            for d in xrange(0,D):
                AH[:,:,d] = m_A[:,:]*m_H[d]
            DIFF = np.reshape( AH - AH1,(M*J*D) )
            DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
            DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
            Crit_AH = (np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(AH1,(M*J*D)) ))**2
            cAH += [Crit_AH]
            AH1[:,:,:] = AH[:,:,:]
            
            if estimateLabels:
                if MFapprox:
                    UtilsC.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
                if not MFapprox:
                    UtilsC.expectation_Z_ParsiMod_RVM_and_CompMod(Sigma_A,m_A,sigma_M,Beta,mu_M,q_Z,neighboursIndexes.astype(np.int32),M,J,K,maxNeighbours)
            else:
                pyhrf.verbose(3, "Using True Z ...")
                TrueZ = read_volume(LabelsFilename)
                for m in xrange(M):
                    q_Z[m,1,:] = np.reshape(TrueZ[0][:,:,:,m],J)
                    q_Z[m,0,:] = 1 - q_Z[m,1,:]
            #ion()
            #figure(6).clf()
            #for m in range(0,M):
                #for k in range(0,K):
                    #z1 = q_Z[m,k,:]
                    #z2 = np.reshape(z1,(l,l))
                    #figure(6)
                    #subplot(M,K,1 + m*K + k)
                    #imshow(z2,interpolation='nearest')
                    #title("m = " + str(m) +"k = " + str(k))
                    #colorbar()
                    #hold(False)
            #draw()

            val = np.reshape(q_Z,(M*K*J))
            val[ np.where((val<=1e-50) & (val>0.0)) ] = 0.0
            #q_Z = np.reshape(val, (M,K,J))

            DIFF = np.reshape( q_Z - q_Z1,(M*K*J) )
            DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
            DIFF[ np.where( (DIFF>-1e-50) & (DIFF<0.0) ) ] = 0.0 #### To avoid numerical problems
            Crit_Z = ( np.linalg.norm(DIFF) / np.linalg.norm( np.reshape(q_Z1,(M*K*J)) ))**2
            cZ += [Crit_Z]
            q_Z1[:,:,:] = q_Z[:,:,:]

            #DIFF = abs(np.reshape(q_Z,(M*K*J)) - np.reshape(q_Z1,(M*K*J)))
            #DIFF[ np.where( (DIFF<1e-50) & (DIFF>0.0) ) ] = 0.0 #### To avoid numerical problems
            #Crit_Z = (sum(DIFF) / len(find(DIFF != 0)))**2
            #cZ += [Crit_Z]
            #q_Z1[:,:,:] = q_Z[:,:,:]
            
            if estimateHRF:
                if estimateSigmaH:
                    pyhrf.verbose(3,"M sigma_H step ...")
                    if gamma_h > 0:
                        sigmaH = vt.maximization_sigmaH_prior(D,Sigma_H,R,m_H,gamma_h)
                    else:
                        sigmaH = vt.maximization_sigmaH(D,Sigma_H,R,m_H)
                    pyhrf.verbose(3,'sigmaH = ' + str(sigmaH))
                    
            mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M,Sigma_A)
            
            for m in xrange(M):
                SUM_q_Z[m] += [sum(q_Z[m,1,:])]
                mu1[m] += [mu_M[m,1]]
                
            UtilsC.maximization_L(Y,m_A,m_H,L,P,XX.astype(np.int32),J,D,M,Ndrift,N)
            PL = np.dot(P,L)
            y_tilde = Y - PL
            if estimateBeta:
                pyhrf.verbose(3,"estimating beta")
                for m in xrange(0,M):
                    if MFapprox:
                        Beta[m] = UtilsC.maximization_beta(beta,q_Z[m,:,:].astype(np.float64),Z_tilde[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
                    if not MFapprox:
                        #Beta[m] = UtilsC.maximization_beta(beta,q_Z[m,:,:].astype(np.float64),q_Z[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
                        Beta[m] = UtilsC.maximization_beta_CB(beta,q_Z[m,:,:].astype(np.float64),J,K,neighboursIndexes.astype(np.int32),gamma,maxNeighbours,MaxItGrad,gradientStep)
                pyhrf.verbose(3,"End estimating beta")
                pyhrf.verbose.printNdarray(3,Beta)
            UtilsC.maximization_sigma_noise(Gamma,PL,sigma_epsilone,Sigma_H,Y,m_A,m_H,Sigma_A,XX.astype(np.int32),J,D,M,N)
            
            #### Computing Free Energy ####
            FreeEnergy1 = FreeEnergy
            FreeEnergy = vt.Compute_FreeEnergy(y_tilde,m_A,Sigma_A,mu_M,sigma_M,m_H,Sigma_H,R,Det_invR,sigmaH,p_Wtilde,tau1,tau2,q_Z,neighboursIndexes,maxNeighbours,Beta,sigma_epsilone,XX,Gamma,Det_Gamma,XGamma,J,D,M,N,K,S,"CompMod")
            Crit_FreeEnergy = (FreeEnergy1 - FreeEnergy) / FreeEnergy1
            FreeEnergy_Iter += [FreeEnergy]
            cFE += [Crit_FreeEnergy]
            
            ni +=1
            t02 = time.time()
            cTime += [t02-t1]
    t2 = time.time()
    
    #FreeEnergyArray = np.zeros((NitMax+1),dtype=np.float64)
    FreeEnergyArray = np.zeros((ni),dtype=np.float64)
    for i in xrange(ni):
        FreeEnergyArray[i] = FreeEnergy_Iter[i]
    #for i in xrange(ni-1,NitMax+1):
        #FreeEnergyArray[i] = FreeEnergy_Iter[ni-1]

    #SUM_q_Z_array = np.zeros((M,NitMax+1),dtype=np.float64)
    #mu1_array = np.zeros((M,NitMax+1),dtype=np.float64)
    SUM_q_Z_array = np.zeros((M,ni),dtype=np.float64)
    mu1_array = np.zeros((M,ni),dtype=np.float64)
    h_norm_array = np.zeros((ni),dtype=np.float64)
    for m in xrange(M):
        for i in xrange(ni):
            SUM_q_Z_array[m,i] = SUM_q_Z[m][i]
            mu1_array[m,i] = mu1[m][i]
            h_norm_array[i] = h_norm[i]
        #for i in xrange(ni-1,NitMax+1):
            #SUM_q_Z_array[m,i] = SUM_q_Z[m][ni-1]
            #mu1_array[m,i] = mu1[m][ni-1]

    
    if PLOT:
        import matplotlib.pyplot as plt
        import matplotlib
        font = {'size'   : 15}
        matplotlib.rc('font', **font)
        plt.savefig('./HRF_Iter_CompMod.png')
        plt.hold(False)
        plt.figure(2)
        #plot(cA[1:-1],'r')
        #hold(True)
        #plot(cH[1:-1],'b')
        #hold(True)
        #plot(cZ[1:-1],'k')
        #hold(True)
        plt.plot(cAH[1:-1],'lightblue')
        plt.hold(True)
        plt.plot(cFE[1:-1],'m')
        plt.hold(False)
        #plt.legend( ('CA','CH', 'CZ', 'CAH', 'CFE') )
        plt.legend( ('CAH', 'CFE') )
        plt.grid(True)
        plt.savefig('./Crit_CompMod.png')
        plt.figure(3)
        plt.plot(FreeEnergyArray)
        plt.grid(True)
        plt.savefig('./FreeEnergy_CompMod.png')

        plt.figure(4)
        for m in xrange(M):
            plt.plot(SUM_q_Z_array[m])
            plt.hold(True)
        plt.hold(False)
        #plt.legend( ('m=0','m=1', 'm=2', 'm=3') )
        #plt.legend( ('m=0','m=1') ) 
        plt.savefig('./Sum_q_Z_Iter_CompMod.png')
        
        plt.figure(5)
        for m in xrange(M):
            plt.plot(mu1_array[m])
            plt.hold(True)
        plt.hold(False)
        plt.savefig('./mu1_Iter_CompMod.png')
        
        plt.figure(6)
        plt.plot(h_norm_array)
        plt.savefig('./HRF_Norm_CompMod.png')
        
        Data_save = xndarray(h_norm_array, ['Iteration'])
        Data_save.save('./HRF_Norm_Comp.nii')        

    CompTime = t2 - t1
    cTimeMean = CompTime/ni
    
    if not NormFlag:
        Norm = np.linalg.norm(m_H)
        m_H /= Norm
        Sigma_H /= Norm**2
        sigmaH /= Norm**2
        m_A *= Norm
        Sigma_A *= Norm**2
        mu_M *= Norm
        sigma_M *= Norm**2
        
    sigma_M = np.sqrt(np.sqrt(sigma_M))
    #+++++++++++++++++++++++  calculate contrast maps and variance +++++++++++++++++++++++#
    if computeContrast:
        if len(contrasts) >0:
            pyhrf.verbose(3, 'Compute contrasts ...')
            nrls_conds = dict([(str(cn), m_A[:,ic]) \
                                   for ic,cn in enumerate(condition_names)] )
            n = 0
            #print contrasts
            #print nrls_conds
            #raw_input('')
            for cname in contrasts:
                #------------ contrasts ------------#
                contrast_expr = AExpr(contrasts[cname], **nrls_conds)
                contrast_expr.check()
                contrast = contrast_expr.evaluate()
                print 
                CONTRAST[:,n] = contrast
                #------------ contrasts ------------#

                #------------ variance -------------#
                ContrastCoef = np.zeros(M,dtype=float)
                ind_conds0 = {}
                for m in xrange(0,M):
                    ind_conds0[condition_names[m]] = 0.0
                for m in xrange(0,M):
                    ind_conds = ind_conds0.copy()
                    ind_conds[condition_names[m]] = 1.0
                    ContrastCoef[m] = eval(contrasts[cname],ind_conds)
                ActiveContrasts = (ContrastCoef != 0) * np.ones(M,dtype=float)
                #print ContrastCoef
                #print ActiveContrasts
                AC = ActiveContrasts*ContrastCoef
                for j in xrange(0,J):
                    S_tmp = Sigma_A[:,:,j]
                    CONTRASTVAR[j,n] = np.dot(np.dot(AC,S_tmp),AC)
                #------------ variance -------------#
                n +=1
                pyhrf.verbose(3, 'Done contrasts computing.')
        #+++++++++++++++++++++++  calculate contrast maps and variance  +++++++++++++++++++++++#
    pyhrf.verbose(1, "Nb iterations to reach criterion: %d" %ni)
    pyhrf.verbose(1, "Computational time = " + str(int( CompTime//60 ) ) + " min " + str(int(CompTime%60)) + " s")
    #print "Computational time = " + str(int( CompTime//60 ) ) + " min " + str(int(CompTime%60)) + " s"
    #print "sigma_H = " + str(sigmaH)
    if pyhrf.verbose.verbosity > 1:
        print 'mu_M:', mu_M
        print 'sigma_M:', sigma_M
        print "sigma_H = " + str(sigmaH)
        print "Beta = " + str(Beta)
        
    StimulusInducedSignal = vt.computeFit(m_H, m_A, X, J, N)
    SNR = 20 * np.log( np.linalg.norm(Y) / np.linalg.norm(Y - StimulusInducedSignal - PL) )
    SNR /= np.log(10.)
    print 'SNR comp =', SNR
    return ni,m_A,m_H, q_Z , sigma_epsilone, mu_M , sigma_M, Beta, L, PL, CONTRAST, CONTRASTVAR, cA[2:],cH[2:],cZ[2:],cAH[2:],cTime[2:],cTimeMean,Sigma_A,StimulusInducedSignal,FreeEnergyArray


def Main_vbjde_Python(graph,Y,Onsets,Thrf,K,TR,beta,dt,scale=1,estimateSigmaH=True,sigmaH = 0.1,NitMax = -1,NitMin = 1,estimateBeta=False,PLOT=False):
    # VEM BOLD classic, using just python
    
    pyhrf.verbose(1,"EM started ...")
    if NitMax < 0:
        NitMax = 100
    gamma = 7.5
    gradientStep = 0.005
    MaxItGrad = 120
    D = int(np.ceil(Thrf/dt))
    M = len(Onsets)
    N = Y.shape[0]
    J = Y.shape[1]
    l = int(np.sqrt(J))
    #-----------------------------------------------------------------------#
    # put neighbour lists into a 2D np array so that it will be easily
    # passed to C-code
    maxNeighbours = max([len(nl) for nl in graph])
    neighboursIndexes = np.zeros((J, maxNeighbours), dtype=np.np.int32)
    neighboursIndexes -= 1
    for i in xrange(J):
        neighboursIndexes[i,:len(graph[i])] = graph[i]
    #-----------------------------------------------------------------------#
    sigma_epsilone = np.ones(J)
    X = OrderedDict([])
    for condition,Ons in Onsets.iteritems():
        X[condition] = vt.compute_mat_X_2(N, TR, D, dt, Ons)
    XX = np.zeros((M,N,D),dtype=np.np.int32)
    nc = 0
    for condition,Ons in Onsets.iteritems():
        XX[nc,:,:] = X[condition]
        nc += 1
    mu_M = np.zeros((M,K),dtype=np.float64)
    sigma_M = 0.5 * np.ones((M,K),dtype=np.float64)
    sigma_M0 = 0.5*np.ones((M,K),dtype=np.float64)
    for k in xrange(1,K):
        mu_M[:,k] = 2.0
    order = 2
    D2 = vt.buildFiniteDiffMatrix(order,D)
    R = np.dot(D2,D2) / pow(dt,2*order)
    Gamma = np.identity(N)
    q_Z = np.zeros((M,K,J),dtype=np.float64)
    #for k in xrange(0,K):
    q_Z[:,1,:] = 1
    Z_tilde = q_Z.copy()
    Sigma_A = np.zeros((M,M,J),np.float64)
    m_A = np.zeros((J,M),dtype=np.float64)
    TT,m_h = getCanoHRF(Thrf-dt,dt) #TODO: check
    for j in xrange(0,J):
        Sigma_A[:,:,j] = 0.01*np.identity(M)
        for m in xrange(0,M):
            for k in xrange(0,K):
                m_A[j,m] += np.random.normal(mu_M[m,k], np.sqrt(sigma_M[m,k]))*Z_tilde[m,k,j]
    m_H = np.array(m_h).astype(np.float64)
    m_H1 = np.array(m_h)
    Sigma_H = np.ones((D,D),dtype=np.float64)
    Beta = beta * np.ones((M),dtype=np.float64)
    zerosDD = np.zeros((D,D),dtype=np.float64)
    zerosD = np.zeros((D),dtype=np.float64)
    zerosND = np.zeros((N,D),dtype=np.float64)
    zerosMM = np.zeros((M,M),dtype=np.float64)
    zerosJMD = np.zeros((J,M,D),dtype=np.float64)
    zerosK = np.zeros(K)
    P = vt.PolyMat( N , 4 , TR)
    zerosP = np.zeros((P.shape[0]),dtype=np.float64)
    L = vt.polyFit(Y, TR, 4,P)
    PL = np.dot(P,L)
    y_tilde = Y - PL
    sigmaH1 = sigmaH
    Crit_H = 1
    Crit_Z = 1
    Crit_A = 1
    cA = []
    cH = []
    cZ = []
    Ndrift = L.shape[0]
    t1 = time.time()
    for ni in xrange(0,NitMin):
        print "------------------------------ Iteration n° " + str(ni+1) + " ------------------------------"
        pyhrf.verbose(2,"------------------------------ Iteration n° " + str(ni+1) + " ------------------------------")
        pyhrf.verbose(3, "E A step ...")
        Sigma_A, m_A = vt.expectation_A(Y,Sigma_H,m_H,m_A,X,Gamma,PL,sigma_M,q_Z,mu_M,D,N,J,M,K,y_tilde,Sigma_A,sigma_epsilone,zerosJMD)
        Sigma_H, m_H = vt.expectation_H(Y,Sigma_A,m_A,X,Gamma,PL,D,R,sigmaH,J,N,y_tilde,zerosND,sigma_epsilone,scale,zerosDD,zerosD)
        pyhrf.verbose(3, "E Z step ...")
        q_Z,Z_tilde = vt.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,graph,M,J,K,zerosK)
        figure(1)
        plot(m_H,'r')
        hold(False)
        draw()
        show()

        if estimateSigmaH:
            pyhrf.verbose(3,"M sigma_H step ...")
            sigmaH = (np.dot(mult(m_H,m_H) + Sigma_H , R )).trace()
            sigmaH /= D
        pyhrf.verbose(3,"M (mu,sigma) step ...")
        mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M,Sigma_A)
        #print mu_M , sigma_M

        L = vt.maximization_L(Y,m_A,X,m_H,L,P,zerosP)
        PL = np.dot(P,L)
        #print L.shape
        #for j in xrange(0,J):
            #print j
            #print '--------------------------'
            #print L[:,j]
            #print '--------------------------'
            #raw_input('')
        #print L
        #raw_input('')
        y_tilde = Y - PL
        if estimateBeta:
            pyhrf.verbose(3,"estimating beta")
            for m in xrange(0,M):
                Beta[m] = vt.maximization_beta(Beta[m],q_Z,Z_tilde,J,K,m,graph,gamma,neighboursIndexes,maxNeighbours)
            print Beta
            pyhrf.verbose(3,"End estimating beta")
            pyhrf.verbose(3,Beta)
        pyhrf.verbose(3,"M sigma noise step ...")
        sigma_epsilone = vt.maximization_sigma_noise(Y,X,m_A,m_H,Sigma_H,Sigma_A,PL,sigma_epsilone,M,zerosMM)
    m_H1[:] = m_H[:]
    q_Z1[:,:,:] = q_Z[:,:,:]
    m_A1[:,:] = m_A[:,:]
    pyhrf.verbose(2,"------------------------------ Iteration n° " + str(ni+2) + " ------------------------------")
    Sigma_A, m_A = vt.expectation_A(Y,Sigma_H,m_H,m_A,X,Gamma,PL,sigma_M,q_Z,mu_M,D,N,J,M,K,y_tilde,Sigma_A,sigma_epsilone,zerosJMD)
    DIFF = abs(np.reshape(m_A,(M*J)) - np.reshape(m_A1,(M*J)))
    Crit_A = sum(DIFF) / len(find(DIFF != 0))
    cA += [Crit_A]
    m_A1[:,:] = m_A[:,:]
    Sigma_H, m_H = vt.expectation_H(Y,Sigma_A,m_A,X,Gamma,PL,D,R,sigmaH,J,N,y_tilde,zerosND,sigma_epsilone,scale,zerosDD,zerosD)
    m_H[0] = 0
    m_H[-1] = 0
    Crit_H = abs(np.mean(m_H - m_H1) / np.mean(m_H))
    cH += [Crit_H]
    m_H1[:] = m_H[:]
    q_Z,Z_tilde = vt.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,graph,M,J,K,zerosK)
    DIFF = abs(np.reshape(q_Z,(M*K*J)) - np.reshape(q_Z1,(M*K*J)))
    Crit_Z = sum(DIFF) / len(find(DIFF != 0))
    cZ += [Crit_Z]
    q_Z1[:,:,:] = q_Z[:,:,:]
    if estimateSigmaH:
        pyhrf.verbose(3,"M sigma_H step ...")
        sigmaH = (np.dot(mult(m_H,m_H) + Sigma_H , R )).trace()
        sigmaH /= D
    mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M,Sigma_A)
    L = vt.maximization_L(Y,m_A,X,m_H,L,P,zerosP)
    PL = np.dot(P,L)
    y_tilde = Y - PL
    if estimateBeta:
        pyhrf.verbose(3,"estimating beta")
        for m in xrange(0,M):
            Beta[m] = vt.maximization_beta(Beta[m],q_Z,Z_tilde,J,K,m,graph,gamma,neighboursIndexes,maxNeighbours)
        pyhrf.verbose(3,"End estimating beta")
        pyhrf.verbose(3,Beta)
    sigma_epsilone = vt.maximization_sigma_noise(Y,X,m_A,m_H,Sigma_H,Sigma_A,PL,sigma_epsilone,M,zerosMM)
    ni += 2
    if (Crit_H > Thresh) and (Crit_Z > Thresh) and (Crit_A > Thresh):
        while ((Crit_H > Thresh) and (Crit_Z > Thresh) and (Crit_A > Thresh) and (ni < NitMax)):# or (ni < 50):
            pyhrf.verbose(2,"------------------------------ Iteration n° " + str(ni+1) + " ------------------------------")
            Sigma_A, m_A = vt.expectation_A(Y,Sigma_H,m_H,m_A,X,Gamma,PL,sigma_M,q_Z,mu_M,D,N,J,M,K,y_tilde,Sigma_A,sigma_epsilone,zerosJMD)
            DIFF = abs(np.reshape(m_A,(M*J)) - np.reshape(m_A1,(M*J)))
            Crit_A = sum(DIFF) / len(find(DIFF != 0))
            m_A1[:,:] = m_A[:,:]
            cA += [Crit_A]
            Sigma_H, m_H = vt.expectation_H(Y,Sigma_A,m_A,X,Gamma,PL,D,R,sigmaH,J,N,y_tilde,zerosND,sigma_epsilone,scale,zerosDD,zerosD)
            m_H[0] = 0
            m_H[-1] = 0
            Crit_H = abs(np.mean(m_H - m_H1) / np.mean(m_H))
            cH += [Crit_H]
            m_H1[:] = m_H[:]
            q_Z,Z_tilde = vt.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,graph,M,J,K,zerosK)
            DIFF = abs(np.reshape(q_Z,(M*K*J)) - np.reshape(q_Z1,(M*K*J)))
            Crit_Z = sum(DIFF) / len(find(DIFF != 0))
            cZ += [Crit_Z]
            q_Z1[:,:,:] = q_Z[:,:,:]
            if estimateSigmaH:
                pyhrf.verbose(3,"M sigma_H step ...")
                sigmaH = (np.dot(mult(m_H,m_H) + Sigma_H , R )).trace()
                sigmaH /= D
            mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M,Sigma_A)
            L = vt.maximization_L(Y,m_A,X,m_H,L,P,zerosP)
            PL = np.dot(P,L)
            y_tilde = Y - PL
            if estimateBeta:
                pyhrf.verbose(3,"estimating beta")
                for m in xrange(0,M):
                    Beta[m] = vt.maximization_beta(Beta[m],q_Z,Z_tilde,J,K,m,graph,gamma,neighboursIndexes,maxNeighbours)
                pyhrf.verbose(3,"End estimating beta")
                pyhrf.verbose(3,Beta)
            sigma_epsilone = vt.maximization_sigma_noise(Y,X,m_A,m_H,Sigma_H,Sigma_A,PL,sigma_epsilone,M,zerosMM)
            ni +=1
    t2 = time.time()
    CompTime = t2 - t1
    
    if PLOT:
        figure(1)
        plot(cA[1:-1],'r')
        hold(True)
        plot(cH[1:-1],'b')
        hold(True)
        plot(cZ[1:-1],'k')
        hold(False)
        legend( ('CA','CH', 'CZ') )
        grid(True)
        draw()
        show()
    Norm = np.linalg.norm(m_H)
    m_H /= Norm
    m_A *= Norm
    mu_M *= Norm
    sigma_M *= Norm
    sigma_M = np.sqrt(sigma_M)
    pyhrf.verbose(1, "Nb iterations to reach criterion: %d" %ni)
    pyhrf.verbose(1, "Computational time = " + str(int( CompTime//60 ) ) + " min " + str(int(CompTime%60)) + " s")
    print "Computational time = " + str(int( CompTime//60 ) ) + " min " + str(int(CompTime%60)) + " s"
    print 'mu_M:', mu_M
    print 'sigma_M:', sigma_M
    print "sigma_H = " + str(sigmaH)
    print "Beta = " + str(Beta)
    return m_A,m_H, q_Z , sigma_epsilone, mu_M , sigma_M, Beta, L, PL


def Main_vbjde(graph,Y,Onsets,Thrf,K,TR,beta,dt,scale=1,estimateSigmaH=True,sigmaH = 0.1,PLOT = False,NitMax = -1,NitMin = 1,hrf = None):
    # VEM BOLD, first version of the code.
    #XXX TODO: To check differences with the other 2 functions
    
    pyhrf.verbose(2,"EM started ...")
    if NitMax < 0:
        NitMax = 100
    D = int(np.ceil(Thrf/dt))
    M = len(Onsets)
    N = Y.shape[0]
    J = Y.shape[1]
    l = int(np.sqrt(J))
    sigma_epsilone = np.ones(J)
    X = OrderedDict([])
    for condition,Ons in Onsets.iteritems():
        X[condition] = vt.compute_mat_X_2(N, TR, D, dt, Ons)
    mu_M = np.zeros((M,K),dtype=float)
    sigma_M = 0.5 * np.ones((M,K),dtype=float)
    mu_M0 = np.zeros((M,K),dtype=float)
    sigma_M0 = np.zeros((M,K),dtype=float)
    for k in xrange(0,K):
        mu_M[:,0] = 2.0
    mu_M0[:,:] = mu_M[:,:]
    sigma_M0[:,:] = sigma_M[:,:]
    #sigmaH = 0.005
    order = 2
    D2 = vt.buildFiniteDiffMatrix(order,D)
    R = np.dot(D2,D2) / pow(dt,2*order)
    Gamma = np.identity(N)
    q_Z = np.zeros((M,K,J),dtype=float)
    for k in xrange(0,K):
        q_Z[:,1,:] = 1
    q_Z1 = q_Z.copy()
    Z_tilde = q_Z.copy()
    Sigma_A = np.zeros((M,M,J),float)
    m_A = np.zeros((J,M),dtype=float)
    TT,m_h = getCanoHRF(Thrf-dt,dt)
    for j in xrange(0,J):
        Sigma_A[:,:,j] = 0.01*np.identity(M)
        for m in xrange(0,M):
            for k in xrange(0,K):
                m_A[j,m] += np.random.normal(mu_M[m,k], np.sqrt(sigma_M[m,k]))*Z_tilde[m,k,j]
    m_H = np.array(m_h)
    m_H1 = np.array(m_h)
    Sigma_H = np.ones((D,D),dtype=float)
    #Sigma_H = 0.1 * np.identity(D)
    Beta = beta * np.ones((M),dtype=float)
    m_A1 = np.zeros((J,M),dtype=float)
    m_A1[:,:] = m_A[:,:]
    Crit_H = [0]
    Crit_Z = [0]
    Crit_sigmaH = [0]
    Hist_sigmaH = []
    ni = 0
    Y_bar_tilde = np.zeros((D),dtype=float)
    zerosND = np.zeros((N,D),dtype=float)
    X_tilde = np.zeros((Y.shape[1],M,D),dtype=float)
    Q_bar = np.zeros(R.shape)
    P = vt.PolyMat( N , 4 , TR)
    L = vt.polyFit(Y, TR, 4,P)
    PL = np.dot(P,L)
    y_tilde = Y - PL
    sigmaH1 = sigmaH

    t1 = time.time()
    while (( (ni < NitMin) or (Crit_sigmaH[-1] > 5e-3) or (Crit_H[-1] > 5e-3) or (Crit_Z[-1] > 5e-3))) \
            and (ni < NitMax):
        #if PLOT:
            #print "------------------------------ Iteration n° " + str(ni+1) + " ------------------------------"
        pyhrf.verbose(2,"------------------------------ Iteration n° " + str(ni+1) + " ------------------------------")
        pyhrf.verbose(3, "E A step ...")
        Sigma_A, m_A = vt.expectation_A(Y,Sigma_H,m_H,m_A,X,Gamma,PL,sigma_M,q_Z,mu_M,D,N,J,M,K,y_tilde,Sigma_A,sigma_epsilone)
        pyhrf.verbose(3,"E H step ...")
        Sigma_H, m_H = vt.expectation_H(Y,Sigma_A,m_A,X,Gamma,PL,D,R,sigmaH,J,N,y_tilde,zerosND,sigma_epsilone,scale)
        Crit_H += [abs(np.mean(m_H - m_H1) / np.mean(m_H))]
        m_H1[:] = m_H[:]
        pyhrf.verbose(3,"E Z step ...")
        q_Z,Z_tilde = vt.expectation_Z(Sigma_A,m_A,sigma_M,Beta,Z_tilde,mu_M,q_Z,graph,M,J,K)
        DIFF = abs(np.reshape(q_Z,(M*K*J)) - np.reshape(q_Z1,(M*K*J)))
        Crit_Z += [np.mean(DIFF) / (DIFF != 0).sum()]
        q_Z1[:,:,:] = q_Z[:,:,:]
        pyhrf.verbose(3,"M (mu,sigma) step ...")
        mu_M , sigma_M = vt.maximization_mu_sigma(mu_M,sigma_M,q_Z,m_A,K,M)
        if estimateSigmaH:
            pyhrf.verbose(3,"M sigma_H step ...")
            sigmaH = np.dot(np.dot(m_H.transpose(),R) , m_H ) + (np.dot(Sigma_H,R)).trace()
            sigmaH /= D
            Crit_sigmaH += [abs((sigmaH - sigmaH1) / sigmaH)]
            Hist_sigmaH += [sigmaH]
            sigmaH1 = sigmaH
        pyhrf.verbose(3,"M L step ...")
        L = vt.maximization_L(Y,m_A,X,m_H,L,P)
        PL = np.dot(P,L)
        y_tilde = Y - PL
        pyhrf.verbose(3,"M sigma_epsilone step ...")
        sigma_epsilone = vt.maximization_sigma_noise(Y,X,m_A,m_H,Sigma_H,Sigma_A,PL,sigma_epsilone,M)
        #if ( (ni+1)% 1) == 0:
        if PLOT:
            from matplotlib import pyplot
            m_Htmp = m_H / np.linalg.norm(m_H)
            hrftmp = hrf / np.linalg.norm(hrf)
            snrH = 20*np.log(1 / np.linalg.norm(m_Htmp - hrftmp))
            #print snrH
            pyplot.clf()
            pyplot.figure(1)
            pyplot.plot(m_H/np.linalg.norm(m_H),'r')
            pyplot.hold(True)
            pyplot.plot(hrf/np.linalg.norm(hrf),'b')
            pyplot.legend( ('Est','Ref') )
            pyplot.title(str(snrH))
            pyplot.hold(False)
            pyplot.draw()
            pyplot.show()
            #figure(2)
            #plot(Hist_sigmaH)
            #title(str(sigmaH))
            ##hold(False)
            #draw()
            #show()
            #for m in range(0,M):
                #for k in range(0,K):
                    #z1 = q_Z[m,k,:];
                    #z2 = np.reshape(z1,(l,l));
                    #figure(2).add_subplot(M,K,1 + m*K + k)
                    #imshow(z2)
                    #title("m = " + str(m) +"k = " + str(k))
            #draw()
            #show()
        ni +=1
    t2 = time.time()
    CompTime = t2 - t1
    Norm = np.linalg.norm(m_H)
    #print Norm
    m_H /= Norm
    m_A *= Norm
    pyhrf.veborse(1, "Nb iterations to reach criterion: %d" %ni)
    pyhrf.verbose(1, "Computational time = " + str(int( CompTime//60 ) ) + " min " + str(int(CompTime%60)) + " s")
    return m_A, m_H, q_Z , sigma_epsilone, (np.array(Hist_sigmaH)).transpose()

