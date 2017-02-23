# -*- coding: utf-8 -*-
"""VEM BOLD Constrained

File that contains function for BOLD data analysis with positivity
and l2-norm=1 constraints.

It imports functions from vem_tools.py in pyhrf/vbjde
"""

import time
import copy
import logging
import os

import os.path as op
import numpy as np

import pyhrf
import pyhrf.vbjde.vem_tools as vt

from pyhrf.boldsynth.hrf import getCanoHRF, genGaussianSmoothHRF
from pyhrf.sandbox.physio_params import PHY_PARAMS_KHALIDOV11, \
                                        linear_rf_operator,\
                                        create_physio_brf, \
                                        create_physio_prf

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

eps = np.spacing(1)

#@profile
def Main_vbjde_physio(graph, Y, Onsets, durations, Thrf, K, TR, beta, dt,
                      scale=1, estimateSigmaH=True, estimateSigmaG=True,
                      sigmaH=0.05, sigmaG=0.05, gamma_h=0, gamma_g=0,
                      NitMax=-1, NitMin=1, estimateBeta=True, PLOT=False,
                      contrasts=[], computeContrast=False,
                      idx_first_tag=0, simulation=None, sigmaMu=None,
                      estimateH=True, estimateG=True, estimateA=True,
                      estimateC=True, estimateZ=True, estimateNoise=True,
                      estimateMP=True, estimateLA=True, use_hyperprior=False,
                      positivity=False, constraint=False, zc=False,
                      phy_params=PHY_PARAMS_KHALIDOV11, prior='omega',
                      H_ini=None, A_ini=None, A_mixtp_ini=None,
                      labels_ini=None, drift_ini=None,
                      noise_var_ini=None):


    logger.info("EM for ASL!")
    np.random.seed(6537540)
    logger.info("data shape: ")
    logger.info(Y.shape)

    Thresh = 1e-5
    D, M = np.int(np.ceil(Thrf / dt)) + 1, len(Onsets)
    #D, M = np.int(np.ceil(Thrf / dt)), len(Onsets)
    N, J = Y.shape[0], Y.shape[1]
    Crit_AH, Crit_CG, cTime, rerror, FE, logL = 1, 1, [], [], [], []
    EP, EPlh, Ent = [],[],[]
    Crit_H, Crit_G, Crit_Z, Crit_A, Crit_C = 1, 1, 1, 1, 1
    cAH, cCG, AH1, CG1 = [], [], [], []
    cA, cC, cH, cG, cZ = [], [], [], [], []
    h_norm, g_norm = [], []
    SUM_q_Z = [[] for m in xrange(M)]
    mua1 = [[] for m in xrange(M)]
    muc1 = [[] for m in xrange(M)]

    # Beta data
    MaxItGrad = 200
    gradientStep = 0.005
    gamma = 7.5
    neighbours_indexes = vt.create_neighbours(graph)
    #maxNeighbours, neighbours_indexes = vt.create_neighbours(graph, J)

    # Control-tag
    w = np.ones((N))
    w[idx_first_tag::2] = -1
    w *= 0.5
    W = np.diag(w)
    # Conditions
    X, XX, condition_names = vt.create_conditions(Onsets, durations, M, N, D, TR, dt)

    if not estimateH:
        zc = False

    # Covariance matrix
    regularizing = False
    order = 2
    if regularizing:
        regularization = np.ones(hrf_len)
        regularization[hrf_len//3:hrf_len//2] = 2
        regularization[hrf_len//2:2*hrf_len//3] = 5
        regularization[2*hrf_len//3:3*hrf_len//4] = 7
        regularization[3*hrf_len//4:] = 10
        # regularization[hrf_len//2:] = 10
    else:
        regularization = None
    d2 = vt.buildFiniteDiffMatrix(order, D, regularization)
    R_inv = d2.T.dot(d2) / pow(dt, 2 * order)
    if zc:
        XX = XX[:, :, 1:-1]    # XX shape (M, N, D)
        R_inv = R_inv[1:-1, 1:-1]
        D = D - 2
    R = np.linalg.inv(R_inv)

    AH1, CG1 = np.zeros((J, M, D)), np.zeros((J, M, D))

    # Noise matrix
    Gamma = np.identity(N)
    # Noise initialization
    sigma_eps = np.ones(J)
    # Labels
    logger.info("Labels are initialized by setting active probabilities "
                "to ones ...")
    q_Z = np.ones((M, K, J), dtype=np.float64) / 2.
    #q_Z = np.zeros((M, K, J), dtype=np.float64)
    #q_Z[:, 1, :] = 1
    q_Z1 = copy.deepcopy(q_Z)

    # H and G
    TT, m_h = getCanoHRF(Thrf, dt)
    H = np.array(m_h[:D]).astype(np.float64)
    if H_ini is not None:
        H = H_ini.copy()
    #H /= np.linalg.norm(H)
    #G = copy.deepcopy(H)
    Omega = linear_rf_operator(len(H) + 6, phy_params, dt, calculating_brf=False)
    G = np.dot(Omega, np.concatenate(([0],[0],[0], H, [0],[0],[0])))[3:-3]
    G /= np.linalg.norm(G)
    Hb = create_physio_brf(phy_params, response_dt=dt, response_duration=Thrf)
    Hb /= np.linalg.norm(Hb)
    Gb = create_physio_prf(phy_params, response_dt=dt, response_duration=Thrf)
    Gb /= np.linalg.norm(Gb)

    if zc:
        Hb = Hb[1:-1]
        Gb = Gb[1:-1]

    if prior=='balloon' and (H_ini is None):
        H = Hb.copy()
        G = Gb.copy()
    G1 = copy.deepcopy(G)

    Mu = Hb.copy()
    H1 = copy.deepcopy(H)
    if estimateH:
        Sigma_H = np.identity(D, dtype=np.float64)
    else:
        Sigma_H = np.zeros((D, D), dtype=np.float64)
    if estimateG:
        Sigma_G = np.identity(D, dtype=np.float64)
    else:
        Sigma_G = np.zeros((D, D), dtype=np.float64)

    normOh = False
    normg = False

    if prior=='omega':
        Omega0 = Omega.copy()
        OmegaH = np.dot(Omega,
                        np.concatenate(([0],[0],[0], H, [0],[0],[0])))[3:-3]
        G = np.dot(Omega, np.concatenate(([0],[0],[0], H, [0],[0],[0])))[3:-3]
        if normOh or normg:
            Omega /= np.linalg.norm(OmegaH)
            OmegaH /=np.linalg.norm(OmegaH)
            G /= np.linalg.norm(G)
    

    
    # Initialize model parameters
    Beta = beta * np.ones((M), dtype=np.float64)
    P = vt.PolyMat(N, 4, TR)
    L = vt.polyFit(Y, TR, 4, P)
    alpha = np.zeros((J), dtype=np.float64)
    WP = np.append(w[:, np.newaxis], P, axis=1)
    AL = np.append(alpha[np.newaxis, :], L, axis=0)
    y_tilde = Y - WP.dot(AL)

    # Parameters Gaussian mixtures
    mu_Ma = 2. * np.append(np.zeros((M, 1)), np.ones((M, 1)), axis=1).astype(np.float64)
    mu_Mc = mu_Ma.copy() / 10.
    sigma_Ma = np.ones((M, K), dtype=np.float64) * 0.3
    sigma_Mc = sigma_Ma.copy() / 100.

    # Params RLs
    if A_ini is not None:
        m_A = A_ini.copy()
    else:
        m_A = np.zeros((J, M), dtype=np.float64)
        for j in xrange(0, J):
            m_A[j, :] = (np.random.normal(mu_Ma, np.sqrt(sigma_Ma)) * q_Z[:, :, j]).sum(axis=1).T
    m_A1 = m_A.copy()
    Sigma_A = np.ones((M, M, J)) * np.identity(M)[:, :, np.newaxis]
    m_C = m_A.copy()
    m_C1 = m_C.copy()
    Sigma_C = Sigma_A.copy()

    #labels_ini=None, drift_ini=None,
    #noise_var_ini=None

    # Precomputations
    WX = W.dot(XX).transpose(1, 0, 2)
    Gamma_X = np.tensordot(Gamma, XX, axes=(1, 1))
    X_Gamma_X = np.tensordot(XX.T, Gamma_X, axes=(1, 0))    # shape(D, M, M, D)
    Gamma_WX = np.tensordot(Gamma, WX, axes=(1, 1))
    XW_Gamma_WX = np.tensordot(WX.T, Gamma_WX, axes=(1, 0)) # shape(D, M, M, D)
    Gamma_WP = Gamma.dot(WP)
    WP_Gamma_WP = WP.T.dot(Gamma_WP)
    sigma_eps_m = np.maximum(sigma_eps, eps)
    cov_noise = sigma_eps_m[:, np.newaxis, np.newaxis]

    ###########################################################################
    #############################################             VBJDE

    free_energy_step = False
    t1 = time.time()
    ni = 0

    while ((ni < NitMin + 1) or (((Crit_FE > Thresh * np.ones_like(Crit_FE)).any()) and (ni < NitMax))):

        logger.info("-------- Iteration n° " + str(ni + 1) + " --------")

        if PLOT and ni >= 0:  # Plotting HRF and PRF
            logger.info("Plotting HRF and PRF for current iteration")
            vt.plot_response_functions_it(ni, NitMin, M, H, G, Mu, prior)


        # Managing types of prior
        logger.info("Prior being used: " + prior)
        priorH_cov_term = np.zeros_like(R_inv)
        priorG_cov_term = np.zeros_like(R_inv)
        matrix_covH = R_inv.copy()
        matrix_covG = R_inv.copy()
        if prior=='balloon':
            logger.info("   prior balloon")
            #matrix_covH = np.eye(R_inv.shape[0], R_inv.shape[1])
            #matrix_covG = np.eye(R_inv.shape[0], R_inv.shape[1])
            priorH_mean_term = np.dot(matrix_covH / sigmaH, Hb)
            priorG_mean_term = np.dot(matrix_covG / sigmaG, Gb)
        elif prior=='omega':
            logger.info("   prior omega")
            #matrix_covG = np.eye(R_inv.shape[0], R_inv.shape[1])
            priorH_mean_term = np.dot(np.dot(Omega[3:-3, 3:-3].T, matrix_covG / sigmaG), G)
            priorH_cov_term = np.dot(np.dot(Omega[3:-3, 3:-3].T, matrix_covG / sigmaG), Omega[3:-3, 3:-3])
            priorG_mean_term = np.dot(matrix_covG / sigmaG, OmegaH)
        elif prior=='hierarchical':
            logger.info("   prior hierarchical")
            matrix_covH = np.eye(R_inv.shape[0], R_inv.shape[1])
            matrix_covG = np.eye(R_inv.shape[0], R_inv.shape[1])
            priorH_mean_term = Mu / sigmaH
            priorG_mean_term = np.dot(Omega[3:-3, 3:-3], Mu / sigmaG)
        else:
            logger.info("   NO prior")
            priorH_mean_term = np.zeros_like(H)
            priorG_mean_term = np.zeros_like(G)


        #####################
        # EXPECTATION
        #####################


        # A
        if estimateA:
            logger.info("E A step ...")
            m_A, Sigma_A = vt.expectation_A_asl(H, G, m_C, W, XX, Gamma, Gamma_X, q_Z, mu_Ma, sigma_Ma, J, y_tilde, Sigma_H, sigma_eps_m)

            cA += [(np.linalg.norm(m_A - m_A1) / np.linalg.norm(m_A1)) ** 2]
            m_A1[:, :] = m_A[:, :]

        if ni > 0 and free_energy_step:
            free_energyA = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyA < free_energy:
                logger.info("free energy has decreased after E-A step from %f to %f", free_energy, free_energyA)

        # C
        if estimateC:
            logger.info("E C step ...")
            m_C, Sigma_C = vt.expectation_C_asl(G, H, m_A, W, XX, Gamma, Gamma_X, q_Z, mu_Mc, sigma_Mc, J, y_tilde, Sigma_G, sigma_eps_m)

            cC += [(np.linalg.norm(m_C - m_C1) / np.linalg.norm(m_C1)) ** 2]
            m_C1[:, :] = m_C[:, :]

        if ni > 0 and free_energy_step:
            free_energyC = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyC < free_energyA:
                logger.info("free energy has decreased after E-C step from %f to %f", free_energyA, free_energyC)


         # Q labels
        if estimateZ:
            logger.info("E Q step ...")
            q_Z = vt.labels_expectation_asl(Sigma_A, m_A, sigma_Ma, mu_Ma,
                                            Sigma_C, m_C, sigma_Mc, mu_Mc,
                                            Beta, q_Z, neighbours_indexes,
                                            M, K, J)
            cZ += [(np.linalg.norm(q_Z - q_Z1) / (np.linalg.norm(q_Z1) + eps)) ** 2]
            q_Z1 = q_Z

        if ni > 0 and free_energy_step:
            free_energyQ = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyQ < free_energyC:
                logger.info("free energy has decreased after E-Q step from %f to %f", free_energyC, free_energyQ)


        # HRF H
        if estimateH:
            logger.info("E H step ...")
            Ht, Sigma_H = vt.expectation_H_asl(Sigma_A, m_A, m_C, G, XX, W, Gamma, Gamma_X, X_Gamma_X, J, y_tilde, cov_noise, matrix_covH, sigmaH, priorH_mean_term, priorH_cov_term)

            if constraint:
                if not np.linalg.norm(Ht)==1:
                    logger.info("   constraint l2-norm = 1")
                    H = vt.constraint_norm1_b(Ht, Sigma_H)
                    #H = Ht / np.linalg.norm(Ht)
                else:
                    logger.info("   l2-norm already 1!!!!!")
                    H = Ht.copy()
                Sigma_H = np.zeros_like(Sigma_H)
            else:
                H = Ht.copy()
                h_norm = np.append(h_norm, np.linalg.norm(H))
                print 'h_norm = ', h_norm

            Crit_H = (np.linalg.norm(H - H1) / np.linalg.norm(H1)) ** 2
            cH += [Crit_H]
            H1[:] = H[:]
            if prior=='omega':
                #OmegaH = np.dot(Omega0, H)
                OmegaH = np.dot(Omega0,
                        np.concatenate(([0],[0],[0], H, [0],[0],[0])))[3:-3]
                Omega = Omega0.copy()
                if normOh:
                    Omega /= np.linalg.norm(OmegaH)
                    OmegaH /= np.linalg.norm(OmegaH)

        if ni > 0 and free_energy_step:
            free_energyH = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyH < free_energyQ:
                logger.info("free energy has decreased after E-H step from %f to %f", free_energyQ, free_energyH)

        # PRF G
        if estimateG:
            logger.info("E G step ...")
            Gt, Sigma_G = vt.expectation_G_asl(Sigma_C, m_C, m_A, H, XX, W, WX, Gamma, Gamma_WX, XW_Gamma_WX, J, y_tilde, cov_noise, matrix_covG, sigmaG, priorG_mean_term, priorG_cov_term)

            if constraint and normg:
                if not np.linalg.norm(Gt)==1:
                    logger.info("   constraint l2-norm = 1")
                    G = vt.constraint_norm1_b(Gt, Sigma_G, positivity=positivity)
                    #G = Gt / np.linalg.norm(Gt)
                else:
                    logger.info("   l2-norm already 1!!!!!")
                    G = Gt.copy()
                Sigma_G = np.zeros_like(Sigma_G)
            else:
                G = Gt.copy()
                g_norm = np.append(g_norm, np.linalg.norm(G))
                print 'g_norm = ', g_norm
            cG += [(np.linalg.norm(G - G1) / np.linalg.norm(G1)) ** 2]
            G1[:] = G[:]


        if ni > 0 and free_energy_step:
            free_energyG = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyG < free_energyA:
                logger.info("free energy has decreased after E-G step from %f to %f", free_energyA, free_energyG)


       # crit. AH and CG
        logger.info("crit. AH and CG")
        AH = m_A[:, :, np.newaxis] * H[np.newaxis, np.newaxis, :]
        CG = m_C[:, :, np.newaxis] * G[np.newaxis, np.newaxis, :]

        Crit_AH = (np.linalg.norm(AH - AH1) / (np.linalg.norm(AH1) + eps)) ** 2
        cAH += [Crit_AH]
        AH1 = AH.copy()
        Crit_CG = (np.linalg.norm(CG - CG1) / (np.linalg.norm(CG1) + eps)) ** 2
        cCG += [Crit_CG]
        CG1 = CG.copy()
        logger.info("Crit_AH = " + str(Crit_AH))
        logger.info("Crit_CG = " + str(Crit_CG))


        #####################
        # MAXIMIZATION
        #####################

        if prior=='balloon':
            logger.info("   prior balloon")
            AuxH = H - Hb
            AuxG = G - Gb
        elif prior=='omega':
            logger.info("   prior omega")
            AuxH = H.copy()
            AuxG = G - np.dot(Omega,
                        np.concatenate(([0],[0],[0], H, [0],[0],[0])))[3:-3]
            #/np.linalg.norm(np.dot(Omega, H))
        elif prior=='hierarchical':
            logger.info("   prior hierarchical")
            AuxH = H - Mu
            AuxG = G - np.dot(Omega,
                        np.concatenate(([0],[0],[0], Mu, [0],[0],[0])))[3:-3]
        else:
            logger.info("   NO prior")
            AuxH = H.copy()
            AuxG = G.copy()

        # Variance HRF: sigmaH
        if estimateSigmaH:
            logger.info("M sigma_H step ...")
            sigmaH = vt.maximization_sigma_asl(D, Sigma_H, matrix_covH, AuxH, use_hyperprior, gamma_h)
            logger.info('sigmaH = ' + str(sigmaH))

        if ni > 0 and free_energy_step:
            free_energyVh = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)

            if free_energyVh < free_energyG:
                logger.info("free energy has decreased after v_h computation from %f to %f", free_energyG, free_energyVh)


        # Variance PRF: sigmaG
        if estimateSigmaG:
            logger.info("M sigma_G step ...")
            print Sigma_H.shape
            print Omega0[3:-3, 3:-3].shape
            print Omega0.shape
            print Omega.shape
            print matrix_covG.shape
            haux = np.dot(np.dot(np.dot(Sigma_H, Omega0[3:-3, 3:-3].T), 
                np.linalg.inv(matrix_covG)), Omega0[3:-3, 3:-3])
            sigmaG = vt.maximization_sigma_asl(D, Sigma_G, matrix_covG, AuxG, 
                use_hyperprior, gamma_g, haux=haux)
            logger.info('sigmaG = ' + str(sigmaG))

        if ni > 0 and free_energy_step:
            free_energyVg = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)

            if free_energyVg < free_energyVh:
                logger.info("free energy has decreased after v_g computation from %f to %f", free_energyVh, free_energyVg)


        # Mu: True HRF in the hierarchical prior case
        if prior=='hierarchical':
            logger.info("M sigma_G step ...")
            Mu = vt.maximization_Mu_asl(H, G, matrix_covH, matrix_covG,
                                     sigmaH, sigmaG, sigmaMu, Omega, R_inv)
            logger.info('sigmaG = ' + str(sigmaG))

        if ni > 0 and free_energy_step:
            free_energyMu = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)

            if free_energyMu < free_energyVg:
                logger.info("free energy has decreased after v_g computation from %f to %f", free_energyVg, free_energyMu)


        # (mu,sigma)
        if estimateMP:
            logger.info("M (mu,sigma) a and c step ...")
            mu_Ma, sigma_Ma = vt.maximization_class_proba(q_Z, m_A, Sigma_A)
            mu_Mc, sigma_Mc = vt.maximization_class_proba(q_Z, m_C, Sigma_C)

        if ni > 0 and free_energy_step:
            free_energyMP = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyMP < free_energyVg:
                logger.info("free energy has decreased after GMM parameters computation from %f to %f", free_energyVg, free_energyMP)


        # Drift L, alpha
        if estimateLA:
            logger.info("M L, alpha step ...")
            AL = vt.maximization_LA_asl(Y, m_A, m_C, XX, WP, W, WP_Gamma_WP, H, G, Gamma)
            y_tilde = Y - WP.dot(AL)

        if ni > 0 and free_energy_step:
            free_energyLA = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyLA < free_energyMP:
                logger.info("free energy has decreased after drifts computation from %f to %f", free_energyMP, free_energyLA)

        # Beta
        if estimateBeta:
            logger.info("M beta step ...")
            for m in xrange(0, M):
                Beta[m], _ = vt.beta_maximization(Beta[m].copy(), q_Z[m, :, :], neighbours_indexes, gamma)
            logger.info(Beta)

        if ni > 0 and free_energy_step:
            free_energyB = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX)
            if free_energyB < free_energyLA:
                logger.info("free energy has decreased after Beta computation from %f to %f", \
                                free_energyLA, free_energyB)

        # Sigma noise
        if estimateNoise:
            logger.info("M sigma noise step ...")
            sigma_eps = vt.maximization_sigma_noise_asl(XX, m_A, Sigma_A, H, m_C, Sigma_C, G, Sigma_H, Sigma_G, W, y_tilde, Gamma, Gamma_X, Gamma_WX, N)

        if PLOT:
            for m in xrange(M):
                SUM_q_Z[m] += [q_Z[m, 1, :].sum()]
                mua1[m] += [mu_Ma[m, 1]]
                muc1[m] += [mu_Mc[m, 1]]


        free_energy = vt.Compute_FreeEnergy(y_tilde, m_A, Sigma_A, mu_Ma, sigma_Ma, H, Sigma_H, AuxH, R, R_inv, sigmaH, sigmaG, m_C, Sigma_C, mu_Mc, sigma_Mc, G, Sigma_G, AuxG, q_Z, neighbours_indexes, Beta, Gamma, gamma, gamma_h, gamma_g, sigma_eps, XX, W, J, D, M, N, K, use_hyperprior, Gamma_X, Gamma_WX, plot=True)
        loglklh = vt.expectation_Ptilde_Likelihood(y_tilde, m_A, Sigma_A, H, Sigma_H, m_C,
                                                      Sigma_C, G, Sigma_G, XX, W, sigma_eps,
                                                      Gamma, J, D, M, N, Gamma_X, Gamma_WX)
        if ni > 0 and free_energy_step:
            if free_energy < free_energyB:
                logger.info("free energy has decreased after Noise computation from %f to %f", free_energyB, free_energy)

        if ni > 0:
            if free_energy < FE[-1]:
                logger.info("WARNING! free energy has decreased in this iteration from %f to %f", FE[-1], free_energy)

        FE += [free_energy]
        logL += [loglklh]

        if ni > 5:
            #Crit_FE = np.abs((FE[-1] - FE[-2]) / FE[-2])
            FE0 = np.array(FE)
            Crit_FE = np.abs((FE0[-5:] - FE0[-6:-1]) / FE0[-6:-1])
            print Crit_FE
            print (Crit_FE > Thresh * np.ones_like(Crit_FE)).any()
        else:
            Crit_FE = 100

        ni += 1
        cTime += [time.time() - t1]

        logger.info("Computing reconstruction error")
        StimulusInducedSignal = vt.computeFit_asl(H, m_A, G, m_C, W, XX)
        rerror = np.append(rerror, np.mean(((Y - StimulusInducedSignal) ** 2).sum(axis=0)) / np.mean((Y ** 2).sum(axis=0)))

    CompTime = time.time() - t1


    # Normalize if not done already
    if not constraint or not normg:
        logger.info("l2-norm of H and G to 1 if not constraint")
        Hnorm = np.linalg.norm(H)
        H /= Hnorm
        Sigma_H /= Hnorm**2
        sigmaH /= Hnorm**2
        m_A *= Hnorm
        Sigma_A *= Hnorm**2
        mu_Ma *= Hnorm
        sigma_Ma *= Hnorm**2
        Gnorm = np.linalg.norm(G)
        G /= Gnorm
        Sigma_G /= Gnorm**2
        sigmaG /= Gnorm**2
        m_C *= Gnorm
        Sigma_C *= Gnorm**2
        mu_Mc *= Gnorm
        sigma_Mc *= Gnorm**2

    if zc:
        H = np.concatenate(([0], H, [0]))
        G = np.concatenate(([0], G, [0]))

    ## Compute contrast maps and variance
    if computeContrast and len(contrasts) > 0:
        logger.info("Computing contrasts ... ")
        CONTRAST_A, CONTRASTVAR_A, \
        CONTRAST_C, CONTRASTVAR_C = vt.compute_contrasts(condition_names, contrasts, m_A, m_C, Sigma_A, Sigma_C, M, J)
    else:
        CONTRAST_A, CONTRASTVAR_A, CONTRAST_C, CONTRASTVAR_C = 0, 0, 0, 0

    #pl_mean = np.mean(WP[:,1:].dot(AL[1:,:]))
    ppm_a_brl, ppm_g_brl, th_ppm_a = vt.ppms_computation(m_A, np.diagonal(Sigma_A), mu_Ma, sigma_Ma, threshold_a="intersect")
    th_ppm_c = 0.05 * np.mean(AL[0, :])
    ppm_a_prl, ppm_g_prl, _ = vt.ppms_computation(m_C, np.diagonal(Sigma_C), mu_Mc, sigma_Mc, threshold_a="bla", threshold_g=th_ppm_c)

    ###########################################################################
    ##########################################    PLOTS and SNR computation

    if PLOT:
        logger.info("plotting...")
        print 'FE = ', FE
        vt.plot_convergence(ni, M, cA, cC, cH, cG, cAH, cCG, SUM_q_Z, mua1, muc1, FE)

    logger.info("Nb iterations to reach criterion: %d",  ni)
    logger.info("Computational time = %s min %s s",
                str(np.int(CompTime // 60)), str(np.int(CompTime % 60)))
    logger.info("Iteration time = %s min %s s",
                str(np.int((CompTime // ni) // 60)), str(np.int((CompTime / ni) % 60)))

    #logger.info("perfusion baseline mean = %f", np.mean(AL[0, :]))
    #logger.info("perfusion baseline var = %f", np.var(AL[0, :]))
    #logger.info("drifts mean = %f", np.mean(AL[1:, :]))
    #logger.info("drifts var = %f", np.var(AL[1:, :]))
    #logger.info("noise mean = %f", np.mean(sigma_eps))
    #logger.info("noise var = %f", np.var(sigma_eps))

    SNR10 = 20 * (np.log10(np.linalg.norm(Y) / \
                np.linalg.norm(Y - StimulusInducedSignal - WP.dot(AL))))
    logger.info("SNR = %f",  SNR10)

    return ni, m_A, H, m_C, G, q_Z, sigma_eps, \
           mu_Ma, sigma_Ma, mu_Mc, sigma_Mc, Beta, AL[1:, :], np.dot(P, AL[1:, :]), \
           AL[0, :], Sigma_A, Sigma_C, Sigma_H, Sigma_G, rerror, \
           CONTRAST_A, CONTRASTVAR_A, CONTRAST_C, CONTRASTVAR_C, \
           ppm_a_brl, ppm_g_brl, ppm_a_prl, ppm_g_prl, \
           cA[:], cH[2:], cC[2:], cG[2:], cZ[2:], cAH[2:], cCG[2:], \
           cTime, FE, logL, th_ppm_a, th_ppm_c

